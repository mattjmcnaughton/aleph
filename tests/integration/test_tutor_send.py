"""Stream behaviour of the send endpoint (AL-220, Phase 2 TDD §5.4-§5.6).

``POST /api/v1/paths/{id}/conversation/messages`` once the turn is admitted:
the frames on the wire, D2's whole-turn-or-nothing atomicity, the D9
reservation, and the posed Tutor check. Everything that can still fail *before*
the stream opens lives in ``test_tutor_send_admission``; the product events the
same lifecycle emits live in ``test_tutor_events``. The shared harness (seeding,
fixtures, the SSE parser, the injected models) is ``_tutor_send_harness``.

Two things about this suite's shape are deliberate:

* **Progressive arrival is asserted structurally, not by timing.** httpx's
  ``ASGITransport`` collects the response body before handing it back, so
  "streaming" here means *the wire contains many ``delta`` frames whose texts
  concatenate to the reply*, not "the first byte arrived early". Wall-clock
  progressiveness through a real socket is what ``compose-smoke`` (§12) and the
  Playwright suite (AL-260) prove; what this tier owns is the protocol.
* **Atomicity is asserted by counting rows, in a fresh session.** D2's rule is
  that a turn exists whole or not at all, so every failure test ends by
  counting ``messages`` (and, for the first turn of a path, ``conversations``)
  rather than by inspecting what the service thinks it did.

Model behaviour is injected at the service's ``_resolve_model`` seam — the same
private-seam patch ``test_paths_api`` uses on the generation orchestrator — so
the real agent, the real router and the real repository all run unchanged.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

import pytest

from aleph import db
from aleph.config import settings
from aleph.models import (
    Attempt,
    Conversation,
    Message,
    MessageRole,
    MessageSource,
    Path,
    User,
)
from aleph.services import tutor as tutor_service
from aleph.services.lifecycle import TutorReplyLimiter
from aleph.services.stub_model import (
    FORCE_TUTOR_CHECK,
    FORCE_TUTOR_FAILURE,
    build_stub_tutor_check,
)

from ._tutor_send_harness import (
    OWNER,
    QUESTION,
    Wire,
    _asgi_scope,
    _blocking_model,
    _body,
    _client,
    _count,
    _integrity_error_on_settle,
    _json_error,
    _malformed_check_model,
    _route_response,
    _seed_owner_and_path,
    _seed_path,
    _seed_turn,
    _send,
    _send_url,
    _sign_in,
    _silent_model,
    _thread,
    _transient_resolver,
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

if TYPE_CHECKING:
    from fastapi import FastAPI


# --------------------------------------------------------------------------- #
# The happy path (W9)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W9")
async def test_full_turn_round_trip_streams_and_persists_the_pair(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """Deltas, then ``done`` with both ids, and the pair at ``max+1``/``max+2``."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)
        await _seed_turn(path_id=path_id, lesson_id=lesson_id)

        wire = await _send(client, _send_url(path_id), _body(lesson_id))

    assert wire.status_code == 200, wire.body
    assert wire.headers["content-type"].startswith("text/event-stream")
    assert wire.headers["cache-control"] == "no-store"
    assert wire.headers["x-accel-buffering"] == "no"

    # Progressive: many deltas, and their concatenation is the whole reply.
    assert len(wire.payloads("delta")) > 1, wire.names
    assert wire.names[-1] == "done", wire.names
    assert QUESTION in wire.text

    done = wire.only("done")
    thread = await _thread(path_id)
    assert [message.position for message in thread] == [1, 2, 3, 4]
    learner, tutor = thread[2], thread[3]
    assert str(learner.id) == done["learner_message_id"]
    assert str(tutor.id) == done["tutor_message_id"]
    assert learner.role is MessageRole.LEARNER
    assert learner.content == QUESTION
    assert learner.source is MessageSource.TYPED
    assert learner.lesson_id == lesson_id
    assert tutor.role is MessageRole.TUTOR
    assert tutor.content == wire.text, "the persisted reply is what was streamed"
    assert tutor.tutor_check is None
    assert tutor.source is None


@pytest.mark.anyio
async def test_first_turn_creates_the_conversation_lazily(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """No conversation row exists until a reply settles (TDD §4/D2)."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)
        assert await _count(Conversation) == 0

        wire = await _send(client, _send_url(path_id), _body(lesson_id))

    assert wire.names[-1] == "done", wire.names
    assert await _count(Conversation) == 1
    assert await _count(Message) == 2


@pytest.mark.anyio
async def test_suggestion_source_is_recorded_on_the_learner_row(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """A suggestion sends as if typed, tagged ``suggestion`` (the §7 entry mix)."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)

        wire = await _send(
            client, _send_url(path_id), _body(lesson_id, source="suggestion")
        )

    assert wire.names[-1] == "done", wire.names
    thread = await _thread(path_id)
    assert thread[0].source is MessageSource.SUGGESTION


# --------------------------------------------------------------------------- #
# Atomicity and failure (D2, §5.6, W14)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W14")
async def test_mid_stream_failure_ends_in_error_and_persists_nothing(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """``[force-tutor-failure]`` raises after deltas are already on the wire."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)

        wire = await _send(
            client,
            _send_url(path_id),
            _body(lesson_id, content=f"{FORCE_TUTOR_FAILURE} {QUESTION}"),
        )

    assert wire.status_code == 200, wire.body
    assert wire.payloads("delta"), "the failure must land mid-stream, after deltas"
    assert wire.names[-1] == "error", wire.names
    assert "done" not in wire.names
    error = wire.only("error")
    assert error["code"] == "upstream_error"
    assert "connection" not in error["message"].lower(), (
        "an upstream failure must not be worded as the learner's network problem"
    )
    # D2: a turn exists whole or not at all.
    assert await _count(Message) == 0
    assert await _count(Conversation) == 0


@pytest.mark.anyio
@pytest.mark.workflow("W14")
async def test_failed_reply_then_retry_succeeds(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """Fail → retry → success with a transient injected stub (D10).

    The e2e tier proves the failure state and the retry affordance; this tier
    owns "the retry actually succeeds", including that the failed attempt left
    nothing behind for the successful one to trip over.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)
        attempts = _transient_resolver(monkeypatch)

        failed = await _send(client, _send_url(path_id), _body(lesson_id))
        assert failed.names[-1] == "error", failed.names
        assert await _count(Message) == 0

        retried = await _send(client, _send_url(path_id), _body(lesson_id))

    assert attempts() == 2
    assert retried.names[-1] == "done", retried.names
    thread = await _thread(path_id)
    assert [message.position for message in thread] == [1, 2]
    assert thread[0].content == QUESTION


@pytest.mark.anyio
async def test_retry_budget_exhaustion_is_a_failed_reply(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """A model that never poses a valid check burns the budget, not the stream."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)
        _use_model(monkeypatch, _malformed_check_model())

        wire = await _send(client, _send_url(path_id), _body(lesson_id))

    assert wire.status_code == 200, wire.body
    assert wire.names[-1] == "error", wire.names
    assert wire.only("error")["code"] == "upstream_error"
    assert "tutor_check" not in wire.names, "a rejected check is never delivered"
    assert await _count(Message) == 0


@pytest.mark.anyio
async def test_a_settle_conflict_is_an_internal_error_and_persists_nothing(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """The one path that reports ``internal_error`` (§5.6), driven end to end.

    A position collision means the per-conversation reservation was somehow
    bypassed — our bug, not the provider's and not the learner's — so it is the
    one failure worded as "something went wrong on our side". D2 still holds
    over it: the reply had already streamed, and none of it is kept.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)
        _integrity_error_on_settle(monkeypatch)

        wire = await _send(client, _send_url(path_id), _body(lesson_id))

    assert wire.status_code == 200, wire.body
    assert wire.payloads("delta"), "the reply streamed; only the settle failed"
    assert wire.names[-1] == "error", wire.names
    assert "done" not in wire.names
    error = wire.only("error")
    assert error["code"] == "internal_error"
    assert "Nothing was saved" in error["message"]
    # D2: the flushed rows went back with the rolled-back transaction.
    assert await _count(Message) == 0
    assert await _count(Conversation) == 0


@pytest.mark.anyio
async def test_timeout_ends_in_error_after_heartbeats(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """A hung provider ends in ``error``, never a dead stream — with pings first.

    The heartbeat and the whole-stream bound are the two halves of §5.4's
    promise: bytes keep flowing so no proxy idle-timeout kills a healthy stream,
    and the stream is bounded so an unhealthy one still terminates.
    """
    monkeypatch.setattr(settings, "sse_heartbeat_seconds", 0.05)
    monkeypatch.setattr(settings, "tutor_reply_timeout", 0.3)

    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)
        _use_model(monkeypatch, _silent_model())

        wire = await _send(client, _send_url(path_id), _body(lesson_id))

    assert wire.status_code == 200, wire.body
    assert wire.comments, "silence must be filled with heartbeats"
    assert all(comment == ": ping" for comment in wire.comments), wire.comments
    assert wire.names == ["error"], wire.names
    assert wire.only("error")["code"] == "timeout"
    assert await _count(Message) == 0


# --------------------------------------------------------------------------- #
# Concurrency (D9)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_concurrent_send_on_one_conversation_is_409_pre_stream(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """One reply in flight per conversation; the second is a JSON ``409``."""
    release = asyncio.Event()
    started = asyncio.Event()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)
        _use_model(monkeypatch, _blocking_model(release, started=started))

        async def first() -> Wire:
            return await _send(client, _send_url(path_id), _body(lesson_id))

        async def second() -> Any:
            # The first reply is genuinely in flight — admitted, reserved, and
            # inside the (blocked) model — the moment this event is set.
            await started.wait()
            try:
                return await _json_error(
                    client, _send_url(path_id), _body(lesson_id, content="second")
                )
            finally:
                release.set()

        wire, conflict = await asyncio.gather(first(), second())

    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "conflict"
    assert wire.names[-1] == "done", wire.names
    # Exactly one turn: the rejected send wrote nothing.
    thread = await _thread(path_id)
    assert [message.content for message in thread][0] == QUESTION
    assert len(thread) == 2


@pytest.mark.anyio
async def test_a_conversation_is_free_again_after_a_reply_settles(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """The reservation is released whether the reply succeeded or failed."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)

        failed = await _send(
            client,
            _send_url(path_id),
            _body(lesson_id, content=f"{FORCE_TUTOR_FAILURE} {QUESTION}"),
        )
        assert failed.names[-1] == "error", failed.names

        second = await _send(client, _send_url(path_id), _body(lesson_id))

    assert second.names[-1] == "done", second.names
    assert tutor_service.tutor_turn_service._replies.in_flight == frozenset()


@pytest.mark.anyio
async def test_distinct_conversations_proceed_under_the_semaphore(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    tutor_flag_enabled: None,
) -> None:
    """Two paths, one permit: both replies land — they queue, they do not fail.

    The per-conversation lock is *per conversation*; the semaphore is what
    bounds the aggregate, and bounding is not refusing.
    """
    monkeypatch.setattr(
        tutor_service.tutor_turn_service,
        "_replies",
        TutorReplyLimiter(max_concurrent=1),
    )
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        first_path, first_lesson = await _seed_path(user_id, topic="Rust ownership")
        second_path, second_lesson = await _seed_path(user_id, topic="Go channels")

        wires = await asyncio.gather(
            _send(client, _send_url(first_path), _body(first_lesson)),
            _send(client, _send_url(second_path), _body(second_lesson)),
        )

    for wire in wires:
        assert wire.names[-1] == "done", wire.names
    assert len(await _thread(first_path)) == 2
    assert len(await _thread(second_path)) == 2


@pytest.mark.anyio
async def test_disconnect_mid_stream_persists_nothing_and_frees_the_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The learner hits **stop**: the client aborts, the server discards (§5.6).

    Driven against the response object rather than through the HTTP client on
    purpose — httpx's ASGI transport runs the app to completion, so it cannot
    abort a stream. Here the ASGI ``receive`` reports ``http.disconnect`` the
    moment the first frame is on the wire, which is exactly what a real server
    does, and Starlette cancels the response's task group around it.

    Two separate cleanups have to hold, and they are owned by two different
    frames: the **reservation** is freed by the response object (so it holds
    even when the generator never ran), and the **producer** — the model call
    and any transaction it might start — is cancelled by the generator's own
    ``finally`` when the abandoned generator is finalised.
    """
    release = asyncio.Event()
    started = asyncio.Event()
    user_id, path_id, lesson_id = await _seed_owner_and_path(username="stopper")
    _use_model(monkeypatch, _blocking_model(release, started=started))

    service = tutor_service.tutor_turn_service
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

    assert any(b"event: delta" in m.get("body", b"") for m in sent), sent
    assert service._replies.in_flight == frozenset(), (
        "the response object frees the conversation when the socket goes away"
    )
    # Finalising the abandoned generator is what cancels the reply task; the
    # producer is still parked inside the (blocked) model, so it never reached
    # a transaction to roll back.
    await response.body_iterator.aclose()  # ty: ignore[unresolved-attribute]
    assert await _count(Message) == 0
    assert await _count(Conversation) == 0
    release.set()


@pytest.mark.anyio
async def test_a_never_started_stream_still_frees_the_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The response is cancelled *before* its body generator is ever stepped.

    The window is real and small: the turn is admitted (and the conversation
    reserved) inside the route, and the client can disconnect before Starlette
    gets as far as the first ``__anext__``. Below ASGI spec 2.4 Starlette races
    ``stream_response`` against ``listen_for_disconnect``; a disconnect that is
    already queued cancels the scope while ``stream_response`` is still parked
    on its first socket write, so the body generator never starts — and an async
    generator that never started never runs its ``finally``, not even when it is
    explicitly closed (PEP 525).

    A cleanup that lived in the generator would therefore leak the reservation
    *permanently*: that conversation would answer ``409`` to every later send
    until the process restarted. The response object's ``finally`` is the frame
    that is actually guaranteed to run, so that is where the release lives.

    ``send`` yields here because a real socket write does; that suspension is
    the whole reason the generator can be cancelled before it is stepped, and
    ``getasyncgenstate`` pins the state rather than trusting the scheduling.
    """
    user_id, path_id, lesson_id = await _seed_owner_and_path(username="ghost")
    _use_model(monkeypatch, _blocking_model(asyncio.Event()))

    service = tutor_service.tutor_turn_service
    async with db.async_session() as session:
        path = await session.get(Path, path_id)
        user = await session.get(User, user_id)
        assert path is not None and user is not None
        response = await _route_response(
            session, path=path, user=user, lesson_id=lesson_id
        )
    assert service._replies.in_flight == frozenset({path_id}), (
        "admission claims the conversation before a response object exists"
    )

    sent: list[Any] = []

    async def send(message: Any) -> None:
        sent.append(message)
        await asyncio.sleep(0)  # a real write suspends; that is the window

    async def receive() -> Any:
        return {"type": "http.disconnect"}

    await response(_asgi_scope(), receive, send)

    body = response.body_iterator
    assert isinstance(body, AsyncGenerator), type(body)
    assert inspect.getasyncgenstate(body) == "AGEN_CREATED", (
        "the body generator must never have been stepped"
    )
    assert not any(m["type"] == "http.response.body" for m in sent), sent
    assert service._replies.in_flight == frozenset()

    # The point of the release: the next send on this conversation is admitted.
    async with db.async_session() as session:
        path = await session.get(Path, path_id)
        assert path is not None
        turn = await service.admit(
            path=path,
            is_admin=False,
            lesson_id=lesson_id,
            content="asking again",
            source=MessageSource.TYPED,
            model_id=settings.model_tutor,
        )
    service.release(turn)
    assert await _count(Message) == 0


@pytest.mark.anyio
async def test_the_request_session_is_closed_before_the_stream_opens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ownership read must not pin a pooled connection for the whole stream.

    ``OwnedPath``'s ``SELECT`` autobegins a transaction, and FastAPI only tears
    a dependency's session down once the response is *finished* — which for this
    route is up to ``TUTOR_REPLY_TIMEOUT`` later. At
    ``MAX_CONCURRENT_TUTOR_REPLIES`` plus everything queued behind them that is
    how a streaming endpoint exhausts the pool for the rest of the API, so the
    route closes the session itself before returning the response.
    """
    user_id, path_id, lesson_id = await _seed_owner_and_path(username="pooled")
    _use_model(monkeypatch, _blocking_model(asyncio.Event()))

    async with db.async_session() as session:
        # Stand in for the ``OwnedPath`` dependency: an ownership read on the
        # request's session, which is what pins the connection.
        path = await session.get(Path, path_id)
        user = await session.get(User, user_id)
        assert path is not None and user is not None
        assert session.in_transaction(), "precondition: the read opened a transaction"

        response = await _route_response(
            session, path=path, user=user, lesson_id=lesson_id
        )

        assert not session.in_transaction(), (
            "the stream must not hold the request's pooled connection"
        )

    # Drain the response so the reservation does not outlive the test.
    async def send(_message: Any) -> None: ...

    async def receive() -> Any:
        return {"type": "http.disconnect"}

    await response(_asgi_scope(), receive, send)
    assert tutor_service.tutor_turn_service._replies.in_flight == frozenset()


# --------------------------------------------------------------------------- #
# The Tutor check (D5)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W12")
async def test_posed_tutor_check_rides_the_wire_and_the_persisted_row(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """``[force-tutor-check]`` → one validated payload, on the event and the row.

    And no Attempt: a Tutor check is the tutor's own non-scoring question, so
    posing one touches no Phase 1 state at all.
    """
    question = f"{FORCE_TUTOR_CHECK} quiz me on this"
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)

        wire = await _send(
            client, _send_url(path_id), _body(lesson_id, content=question)
        )

    assert wire.names[-1] == "done", wire.names
    expected = dict(build_stub_tutor_check("quiz me on this"))
    posed = wire.only("tutor_check")
    assert posed == {**expected, "answered_index": None}
    # The check is delivered *before* the reply text it accompanies.
    assert wire.names.index("tutor_check") < wire.names.index("delta")

    thread = await _thread(path_id)
    assert thread[1].tutor_check == {**expected, "answered_index": None}
    assert thread[0].tutor_check is None
    assert await _count(Attempt) == 0
