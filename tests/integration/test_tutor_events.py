"""The tutor's product events are emitted by the real turn lifecycle (AL-240).

Phase 2 TDD §9's five events, asserted where they are actually produced: the
send endpoint's admission/stream/settle lifecycle and the check-answer route,
against real Postgres and the streamed stub, with ``capfire`` capturing what
lands in Logfire. This closes the same loop ``test_product_events`` closes for
Phase 1 — ``test_events`` pins the manifest to the emitters,
``test_metrics_queries`` pins the §7 queries to the manifest, and this proves a
learner acting on the real surface emits those fields.

The failure fixtures are AL-220's, reused from ``_tutor_send_harness`` rather
than re-injected: the point of ``tutor_reply_completed`` is that it fires on
*every* resolution, so it has to be asserted on the very streams that already
prove nothing is persisted (D2).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from aleph import db, events
from aleph.config import settings
from aleph.logging import configure_logging
from aleph.models import Path, User
from aleph.services.stub_model import (
    FORCE_TUTOR_CHECK,
    FORCE_TUTOR_FAILURE,
    FORCE_TUTOR_REFUSAL,
)

from ._tutor_send_harness import (
    OWNER,
    QUESTION,
    _asgi_scope,
    _blocking_model,
    _body,
    _client,
    _json_error,
    _route_response,
    _seed_owner_and_path,
    _seed_path,
    _seed_turn,
    _send,
    _send_url,
    _sign_in,
    _silent_model,
    _thread,
    _use_model,
)
from ._tutor_send_harness import (
    app as app,  # noqa: PLC0414 - re-exported so the fixture resolves here
)
from ._tutor_send_harness import (
    isolated_reply_limiter as isolated_reply_limiter,  # noqa: PLC0414
)
from ._tutor_send_harness import (
    stub_tutor_model as stub_tutor_model,  # noqa: PLC0414
)
from .conftest import assert_event, captured_records

if TYPE_CHECKING:
    import uuid

    from fastapi import FastAPI


# --------------------------------------------------------------------------- #
# The turn lifecycle (W9)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W9")
async def test_a_settled_turn_emits_the_whole_lifecycle(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None, capfire
) -> None:
    """Sent → conversation started → reply completed, all fully stamped.

    Every one carries the lesson locator (account, path, lesson, position),
    because every §7 tutor metric is per-lesson — the primary one compares
    continuation for lessons *with* a tutor message against lessons without.
    """
    configure_logging()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)

        wire = await _send(client, _send_url(path_id), _body(lesson_id))

    assert wire.names[-1] == "done", wire.names

    sent = assert_event(capfire, events.TUTOR_MESSAGE_SENT)
    assert sent["account_id"] == str(user_id)
    assert sent["path_id"] == str(path_id)
    assert sent["lesson_id"] == str(lesson_id)
    assert sent["position_in_path"] == 1
    assert sent["source"] == "typed"
    assert sent["workflow"] == "W9"

    started = assert_event(capfire, events.TUTOR_CONVERSATION_STARTED)
    assert started["path_id"] == str(path_id)
    assert started["lesson_id"] == str(lesson_id)

    completed = assert_event(capfire, events.TUTOR_REPLY_COMPLETED)
    assert completed["outcome"] == "success"
    assert completed["success"] is True
    assert completed["workflow"] == "W9"
    assert completed["ttft_ms"] >= 0, "the stub streamed deltas, so TTFT exists"
    assert completed["duration_ms"] >= completed["ttft_ms"]
    assert completed["total_tokens"] > 0, (
        "usage must ride the event, or the token panel silently reads zero"
    )


@pytest.mark.anyio
async def test_conversation_started_fires_once_per_path(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None, capfire
) -> None:
    """The row is created lazily on the first settled turn; the second reuses it."""
    configure_logging()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)

        first = await _send(client, _send_url(path_id), _body(lesson_id))
        second = await _send(client, _send_url(path_id), _body(lesson_id))

    assert first.names[-1] == "done" and second.names[-1] == "done"
    assert len(captured_records(capfire, events.TUTOR_CONVERSATION_STARTED)) == 1
    assert len(captured_records(capfire, events.TUTOR_MESSAGE_SENT)) == 2
    assert len(captured_records(capfire, events.TUTOR_REPLY_COMPLETED)) == 2


@pytest.mark.anyio
async def test_the_entry_mix_datum_rides_the_sent_event(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None, capfire
) -> None:
    """``source`` is what ``tutor_entry_mix.sql`` groups on (§7)."""
    configure_logging()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)

        wire = await _send(
            client, _send_url(path_id), _body(lesson_id, source="suggestion")
        )

    assert wire.names[-1] == "done", wire.names
    assert assert_event(capfire, events.TUTOR_MESSAGE_SENT)["source"] == "suggestion"


@pytest.mark.anyio
async def test_a_refused_send_never_became_a_turn(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None, capfire
) -> None:
    """A pre-admission refusal emits nothing — it is not an asked question.

    ``tutor_message_sent`` is the adoption/primary-metric signal, so a send the
    endpoint refused before it ever became a turn (here: an ungenerated lesson)
    must not count as one.
    """
    configure_logging()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id, generated=False)

        response = await client.post(_send_url(path_id), json=_body(lesson_id))

    assert response.status_code == 409, response.text
    assert captured_records(capfire, events.TUTOR_MESSAGE_SENT) == []
    assert captured_records(capfire, events.TUTOR_REPLY_COMPLETED) == []


@pytest.mark.anyio
async def test_a_rate_limited_send_never_became_a_turn(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None, capfire
) -> None:
    """The daily cap refuses pre-stream, so the capped ask is not a sent message.

    The seeded turn is written straight to the database, never through the
    endpoint, so every event here would have to come from the refused request.
    A cap that still counted its refusals would inflate adoption and depth with
    questions the tutor never saw.
    """
    configure_logging()
    monkeypatch.setattr(settings, "rate_limit_tutor_messages_per_day", 1)
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)
        await _seed_turn(path_id=path_id, lesson_id=lesson_id)

        response = await _json_error(client, _send_url(path_id), _body(lesson_id))

    assert response.status_code == 429, response.text
    assert captured_records(capfire, events.TUTOR_MESSAGE_SENT) == []
    assert captured_records(capfire, events.TUTOR_REPLY_COMPLETED) == []


@pytest.mark.anyio
async def test_a_send_refused_as_busy_emits_nothing_of_its_own(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None, capfire
) -> None:
    """One reply in flight per conversation (D9): the 409'd send emits nothing.

    The first send's events are the only ones on the wire — exactly one sent and
    one completed, both belonging to the reply that ran. The rejected send never
    reached admission, so counting it would double the learner's one question.
    """
    configure_logging()
    release, started = asyncio.Event(), asyncio.Event()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)
        _use_model(monkeypatch, _blocking_model(release, started=started))

        async def first() -> Any:
            return await _send(client, _send_url(path_id), _body(lesson_id))

        async def second() -> Any:
            await started.wait()
            try:
                return await _json_error(
                    client, _send_url(path_id), _body(lesson_id, content="second")
                )
            finally:
                release.set()

        wire, conflict = await asyncio.gather(first(), second())

    assert conflict.status_code == 409, conflict.text
    assert wire.names[-1] == "done", wire.names
    assert len(captured_records(capfire, events.TUTOR_MESSAGE_SENT)) == 1
    assert len(captured_records(capfire, events.TUTOR_REPLY_COMPLETED)) == 1


# --------------------------------------------------------------------------- #
# Every resolution, including the ones that persist nothing (W14, D2)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W14")
async def test_a_mid_stream_failure_still_completes_with_its_latency(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None, capfire
) -> None:
    """The failure fires the event too — that *is* the failure-rate metric.

    D2 persists nothing here, so the event seam is the only record the reply
    ever happened. TTFT is populated because the failure lands after deltas: a
    reply that streamed for two seconds and then died is a different operational
    story from one that never produced a token, and the guardrail has to be able
    to tell them apart. The learner's question still counts as sent.
    """
    configure_logging()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)

        wire = await _send(
            client,
            _send_url(path_id),
            _body(lesson_id, content=f"{FORCE_TUTOR_FAILURE} {QUESTION}"),
        )

    assert wire.names[-1] == "error", wire.names
    completed = assert_event(capfire, events.TUTOR_REPLY_COMPLETED)
    assert completed["outcome"] == "failure"
    assert completed["success"] is False
    assert completed["workflow"] == "W14"
    assert completed["ttft_ms"] >= 0, "deltas were on the wire before it failed"
    assert completed["duration_ms"] >= 0
    # The turn was asked; nothing settled, so no conversation was started.
    assert len(captured_records(capfire, events.TUTOR_MESSAGE_SENT)) == 1
    assert captured_records(capfire, events.TUTOR_CONVERSATION_STARTED) == []


@pytest.mark.anyio
async def test_a_refusal_resolves_as_a_success(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None, capfire
) -> None:
    """An over-the-boundary ask, answered gracefully, is ``success`` (D5).

    The whole point of the failure-rate guardrail is that it means "the tutor
    broke", never "the tutor declined": a refusal is a real, persisted turn and
    is deliberately not machine-tagged this phase (PRD §5.7b). Filing it as a
    failure would make an in-policy tutor look like an outage.
    """
    configure_logging()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)

        wire = await _send(
            client,
            _send_url(path_id),
            _body(lesson_id, content=f"{FORCE_TUTOR_REFUSAL} write my exam for me"),
        )

    assert wire.names[-1] == "done", wire.names
    completed = assert_event(capfire, events.TUTOR_REPLY_COMPLETED)
    assert (completed["outcome"], completed["success"]) == ("success", True)
    assert completed["workflow"] == "W9", "a refusal is not a failure workflow"
    assert len(await _thread(path_id)) == 2, "a refusal is a real, persisted turn"


@pytest.mark.anyio
async def test_a_reply_that_never_produced_a_token_has_no_ttft(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    tutor_flag_enabled: None,
    capfire,
) -> None:
    """A hung provider times out with ``ttft_ms`` null, not zero.

    Zero would be indistinguishable from an instant first token and would drag
    the TTFT p95 panel down exactly when the tutor is at its slowest.

    OTEL attributes cannot hold ``None``, so logfire carries it as the JSON text
    ``null`` (with a ``logfire.json_schema`` entry) — which is why the saved
    queries read TTFT through ``nullif(attributes ->> 'ttft_ms', 'null')``
    rather than a bare cast that would choke. Asserting both spellings here is
    what keeps that query defence tied to the real wire shape.
    """
    configure_logging()
    monkeypatch.setattr(settings, "sse_heartbeat_seconds", 0.05)
    monkeypatch.setattr(settings, "tutor_reply_timeout", 0.3)
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)
        _use_model(monkeypatch, _silent_model())

        wire = await _send(client, _send_url(path_id), _body(lesson_id))

    assert wire.only("error")["code"] == "timeout"
    completed = assert_event(capfire, events.TUTOR_REPLY_COMPLETED)
    assert completed["outcome"] == "failure"
    assert completed["ttft_ms"] in (None, "null"), completed["ttft_ms"]
    assert completed["duration_ms"] > 0


@pytest.mark.anyio
async def test_a_stopped_reply_is_recorded_as_stopped_not_failed(
    monkeypatch: pytest.MonkeyPatch, capfire
) -> None:
    """The learner hits **stop**: ``stopped``, on W9, with its TTFT intact.

    Filing an abort as ``failure`` would put learner behaviour straight into the
    reply-failure guardrail — the number that is supposed to say whether *we*
    are breaking. Driven against the response object because httpx's ASGI
    transport cannot abort a stream (see the harness).
    """
    configure_logging()
    release, started = asyncio.Event(), asyncio.Event()
    user_id, path_id, lesson_id = await _seed_owner_and_path(username="ev-stopper")
    _use_model(monkeypatch, _blocking_model(release, started=started))

    async with db.async_session() as session:
        path = await session.get(Path, path_id)
        user = await session.get(User, user_id)
        assert path is not None and user is not None
        response = await _route_response(
            session, path=path, user=user, lesson_id=lesson_id
        )

    sent: list[Any] = []
    streaming = asyncio.Event()

    async def send(message: Any) -> None:
        sent.append(message)
        if message["type"] == "http.response.body" and message["body"]:
            streaming.set()

    async def receive() -> Any:
        await streaming.wait()
        return {"type": "http.disconnect"}

    await response(_asgi_scope(), receive, send)
    # Finalising the abandoned generator is what cancels the reply task.
    await response.body_iterator.aclose()  # ty: ignore[unresolved-attribute]
    release.set()

    completed = assert_event(capfire, events.TUTOR_REPLY_COMPLETED)
    assert completed["outcome"] == "stopped"
    assert completed["success"] is False
    assert completed["workflow"] == "W9", "an abort is not a failure workflow"
    assert completed["ttft_ms"] >= 0, "a delta reached the wire before the stop"
    assert captured_records(capfire, events.TUTOR_CONVERSATION_STARTED) == []


# --------------------------------------------------------------------------- #
# The Tutor check (W12)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W12")
async def test_a_posed_check_is_shown_and_then_answered(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None, capfire
) -> None:
    """``tutor_check_shown`` on the posed card, ``tutor_check_answered`` on the tap."""
    configure_logging()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)

        wire = await _send(
            client,
            _send_url(path_id),
            _body(lesson_id, content=f"{FORCE_TUTOR_CHECK} quiz me on this"),
        )
        assert wire.names[-1] == "done", wire.names
        message_id, correct_index = await _posed_check(path_id)

        answered = await client.post(
            f"/api/v1/messages/{message_id}/tutor-check-answer",
            json={"selected_index": correct_index},
        )
        assert answered.status_code == 204, answered.text

    shown = assert_event(capfire, events.TUTOR_CHECK_SHOWN)
    assert shown["path_id"] == str(path_id)
    assert shown["lesson_id"] == str(lesson_id)
    assert shown["position_in_path"] == 1
    assert shown["workflow"] == "W12"

    event = assert_event(capfire, events.TUTOR_CHECK_ANSWERED)
    assert event["account_id"] == str(user_id)
    assert event["path_id"] == str(path_id)
    assert event["lesson_id"] == str(lesson_id)
    assert event["position_in_path"] == 1
    assert event["outcome"] == "correct"
    assert event["is_correct"] is True
    assert event["first_answer"] is True
    assert event["workflow"] == "W12"


@pytest.mark.anyio
@pytest.mark.workflow("W12")
async def test_a_re_answer_re_emits_tagged_as_not_the_first(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None, capfire
) -> None:
    """The AL-240 decision, pinned: last-wins **and** an event per real write.

    A Tutor-check re-answer genuinely rewrites the stored payload (AL-221 made
    it last-wins, unlike the Quick check's first-wins Attempt, which writes
    nothing on a repeat submit). So it emits — and ``first_answer`` is what lets
    any per-check rate exclude the second one, which is how §7's uptake metric
    stays honest with re-answers allowed.
    """
    configure_logging()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)

        wire = await _send(
            client,
            _send_url(path_id),
            _body(lesson_id, content=f"{FORCE_TUTOR_CHECK} quiz me on this"),
        )
        assert wire.names[-1] == "done", wire.names
        message_id, correct_index = await _posed_check(path_id)
        wrong_index = 0 if correct_index != 0 else 1
        url = f"/api/v1/messages/{message_id}/tutor-check-answer"

        first_post = await client.post(url, json={"selected_index": wrong_index})
        assert first_post.status_code == 204, first_post.text
        second = await client.post(url, json={"selected_index": correct_index})
        assert second.status_code == 204, second.text

    records = captured_records(capfire, events.TUTOR_CHECK_ANSWERED)
    assert len(records) == 2, "a re-answer is a real write, so it is a real event"
    first, again = (record["attributes"] for record in records)
    assert (first["outcome"], first["is_correct"], first["first_answer"]) == (
        "incorrect",
        False,
        True,
    )
    assert (again["outcome"], again["is_correct"], again["first_answer"]) == (
        "correct",
        True,
        False,
    )


async def _posed_check(path_id: uuid.UUID) -> tuple[uuid.UUID, int]:
    """The tutor message carrying a Tutor check, and that check's correct index."""
    thread = await _thread(path_id)
    message = next(entry for entry in thread if entry.tutor_check is not None)
    check = message.tutor_check
    assert check is not None
    return message.id, int(check["correct_index"])
