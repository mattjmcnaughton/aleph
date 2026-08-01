"""Shaping's product events are emitted by the real surfaces (AL-340, TDD §9).

Phase 2B's six events, asserted where they are actually produced: the send
endpoint's admission/stream/settle lifecycle, the mid-stream proposal frame, and
the apply/undo routes — against real Postgres and the streamed stub, with
``capfire`` capturing what lands in Logfire.

This is the third rung of the same three-test loop 2A closed. ``test_events``
pins the manifest to the emitters, ``test_metrics_queries`` pins the §7 queries
to the manifest, and this proves a learner acting on the *real* surface emits
those fields — in particular the two that no unit test can prove are true:
``account_id`` (threaded from the router's ownership walk) and
``change_applied``'s ``lesson_ids`` (the ids apply really wrote).

The failure and decline fixtures are AL-320's D12 sentinels, reused rather than
re-injected: the point of ``shaping_reply_completed`` is that it fires on *every*
resolution, so it has to be asserted on the very streams that already prove
nothing is persisted.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select

from aleph import db, events
from aleph.models import Lesson, PathChange, PathStatus
from aleph.services import generation as gen_module
from aleph.services import shaping as shaping_service
from aleph.services.shaping import PathApplyLock
from aleph.services.stub_model import (
    FORCE_PROPOSAL_ADD,
    FORCE_PROPOSAL_REVISE,
    FORCE_SHAPING_DECLINE,
    FORCE_SHAPING_FAILURE,
    SHAPING_REVISION_INSTRUCTION,
)

from ._shaping_send_harness import (
    ASK,
    OWNER,
    _body,
    _client,
    _conversation_url,
    _json_error,
    _seed_path,
    _seed_shaping_turn,
    _send,
    _send_url,
    _sign_in,
)
from ._shaping_send_harness import (
    app as app,  # noqa: PLC0414 - re-exported so the fixture resolves here
)
from ._shaping_send_harness import (
    isolated_shaping_limiter as isolated_shaping_limiter,  # noqa: PLC0414
)
from ._shaping_send_harness import (
    stub_shaping_model as stub_shaping_model,  # noqa: PLC0414
)
from .conftest import (
    CollectingSpawn,
    assert_event,
    captured_records,
    stub_resolver,
)

if TYPE_CHECKING:
    import uuid

    from fastapi import FastAPI


# --------------------------------------------------------------------------- #
# Fixtures & helpers (test_shaping_apply's, minus everything not needed here)
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def spawn(monkeypatch: pytest.MonkeyPatch) -> CollectingSpawn:
    """The generation singleton's seams: the stub model + a drainable spawn.

    Autouse because apply answers with the refreshed path through the same read
    seam ``GET /paths/{id}`` uses, and that seam is a *trigger* — without this a
    test would spawn a real generation just by applying a proposal.
    """
    collector = CollectingSpawn()
    monkeypatch.setattr(
        gen_module.generation_orchestrator, "_resolve_model", stub_resolver()
    )
    monkeypatch.setattr(gen_module.generation_orchestrator, "_spawn", collector)
    return collector


@pytest.fixture(autouse=True)
def isolated_apply_locks(monkeypatch: pytest.MonkeyPatch) -> PathApplyLock:
    """A fresh per-path lock registry per test (an ``asyncio.Lock`` binds a loop)."""
    locks = PathApplyLock()
    monkeypatch.setattr(shaping_service.shaping_change_service, "_locks", locks)
    return locks


def _ask(sentinel: str) -> str:
    """A learner ask carrying one of the D12 shaping sentinels."""
    return f"{sentinel} {ASK}"


def _addition(
    *,
    insert_at_position: int = 2,
    titles: tuple[str, ...] = ("Borrowing in practice", "Lifetimes, gently"),
    new_unit: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "insert_at_position": insert_at_position,
        "lessons": [{"title": title} for title in titles],
        "rationale": "The path does not cover this yet.",
        "estimated_minutes": 10,
        "new_unit": new_unit,
    }


def _revision(*, lesson_id: uuid.UUID) -> dict[str, Any]:
    return {
        "lesson_id": str(lesson_id),
        "instruction": SHAPING_REVISION_INSTRUCTION,
        "rationale": "You have not started this lesson yet.",
        "new_title": None,
    }


def _proposal(*operations: dict[str, Any], summary: str = "Adds lessons.") -> Any:
    return {"operations": list(operations), "summary": summary}


def _apply_url(message_id: uuid.UUID | str) -> str:
    return f"/api/v1/messages/{message_id}/apply-proposal"


def _undo_url(change_id: uuid.UUID | str) -> str:
    return f"/api/v1/changes/{change_id}/undo"


async def _lesson_ids(path_id: uuid.UUID) -> list[str]:
    """Every lesson id on ``path_id``, in ``position_in_path`` order."""
    async with db.async_session() as session:
        rows = await session.execute(
            select(Lesson)
            .where(Lesson.path_id == path_id)
            .order_by(Lesson.position_in_path)
        )
        return [str(lesson.id) for lesson in rows.scalars()]


async def _change_id(path_id: uuid.UUID) -> uuid.UUID:
    async with db.async_session() as session:
        rows = await session.execute(
            select(PathChange).where(PathChange.path_id == path_id)
        )
        return rows.scalars().one().id


# --------------------------------------------------------------------------- #
# The turn lifecycle (W17)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W17")
async def test_a_settled_shaping_turn_emits_the_whole_lifecycle(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None, capfire
) -> None:
    """Sent → conversation started → reply completed, all stamped path-level.

    The ``account_id`` assertion is the load-bearing one: ``AdmittedShapingTurn``
    carries it only because this ticket reads it, and it is threaded from the
    owned path the router resolved — not re-read, not guessed.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)

        wire = await _send(client, _send_url(path_id), _body())

    assert wire.names[-1] == "done", wire.names

    sent = assert_event(capfire, events.SHAPING_MESSAGE_SENT)
    assert sent["account_id"] == str(user_id)
    assert sent["path_id"] == str(path_id)
    assert sent["source"] == "typed"
    assert sent["workflow"] == "W17"
    # Path-level: the in-lesson locator is deliberately absent (PRD §5.1).
    assert "lesson_id" not in sent
    assert "position_in_path" not in sent

    started = assert_event(capfire, events.SHAPING_CONVERSATION_STARTED)
    assert started["account_id"] == str(user_id)
    assert started["path_id"] == str(path_id)

    completed = assert_event(capfire, events.SHAPING_REPLY_COMPLETED)
    assert completed["outcome"] == "success"
    assert completed["success"] is True
    assert completed["has_proposal"] is False
    assert completed["duration_ms"] >= 0
    assert completed["ttft_ms"] is not None


@pytest.mark.anyio
async def test_shaping_conversation_started_fires_once_per_path(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None, capfire
) -> None:
    """Two turns, one thread: the lazy upsert's ``created`` flag is the gate."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)

        await _send(client, _send_url(path_id), _body())
        await _send(client, _send_url(path_id), _body("And after that?"))

    assert len(captured_records(capfire, events.SHAPING_CONVERSATION_STARTED)) == 1
    assert len(captured_records(capfire, events.SHAPING_MESSAGE_SENT)) == 2
    assert len(captured_records(capfire, events.SHAPING_REPLY_COMPLETED)) == 2


@pytest.mark.anyio
async def test_the_entry_mix_datum_rides_the_sent_event(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None, capfire
) -> None:
    """The rail's four §5.3 suggestions are ``suggestion``, not ``typed``."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)

        await _send(client, _send_url(path_id), _body(source="suggestion"))

    assert assert_event(capfire, events.SHAPING_MESSAGE_SENT)["source"] == "suggestion"


@pytest.mark.anyio
async def test_a_send_refused_before_admission_never_became_a_turn(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None, capfire
) -> None:
    """A non-``ready`` path is a pre-stream ``409``: nothing was ever asked."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id, status=PathStatus.GENERATING)

        response = await _json_error(client, _send_url(path_id), _body())

    assert response.status_code == 409
    assert captured_records(capfire, events.SHAPING_MESSAGE_SENT) == []
    assert captured_records(capfire, events.SHAPING_REPLY_COMPLETED) == []


@pytest.mark.anyio
async def test_a_mid_stream_failure_still_completes_with_its_latency(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None, capfire
) -> None:
    """The turn was asked (D2 persists nothing) and the reply resolved ``failure``.

    The honest denominator for the failure guardrail: the ``sent`` event stands,
    no conversation was started, and the resolution is filed as a failure.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)

        wire = await _send(
            client, _send_url(path_id), _body(_ask(FORCE_SHAPING_FAILURE))
        )

    assert wire.names[-1] == "error", wire.names

    completed = assert_event(capfire, events.SHAPING_REPLY_COMPLETED)
    assert completed["outcome"] == "failure"
    assert completed["success"] is False
    assert completed["has_proposal"] is False
    assert len(captured_records(capfire, events.SHAPING_MESSAGE_SENT)) == 1
    assert captured_records(capfire, events.SHAPING_CONVERSATION_STARTED) == []


@pytest.mark.anyio
@pytest.mark.workflow("W20")
async def test_a_declined_edit_resolves_as_a_success_with_no_proposal(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None, capfire
) -> None:
    """W20 tags nothing: a decline is an ordinary, persisted, successful turn.

    It is distinguishable in the events only by ``has_proposal=False`` — which is
    also what a plain conversational turn looks like. That limit is deliberate
    (the decline is not machine-tagged this phase, D5) and documented.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)

        wire = await _send(
            client, _send_url(path_id), _body(_ask(FORCE_SHAPING_DECLINE))
        )

    assert wire.names[-1] == "done", wire.names

    completed = assert_event(capfire, events.SHAPING_REPLY_COMPLETED)
    assert completed["outcome"] == "success"
    assert completed["has_proposal"] is False
    assert captured_records(capfire, events.PROPOSAL_SHOWN) == []


# --------------------------------------------------------------------------- #
# The Proposal card (W17 / W18)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W17")
async def test_an_addition_proposal_is_shown_with_its_counts(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None, capfire
) -> None:
    """``n_add_lessons`` counts lessons, and the reply is flagged as carrying one."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)

        wire = await _send(client, _send_url(path_id), _body(_ask(FORCE_PROPOSAL_ADD)))

    [card] = wire.payloads("proposal")
    proposed = sum(len(op["lessons"]) for op in card["operations"] if "lessons" in op)

    assert proposed > 0, card

    shown = assert_event(capfire, events.PROPOSAL_SHOWN)
    assert shown["account_id"] == str(user_id)
    assert shown["path_id"] == str(path_id)
    assert shown["n_add_lessons"] == proposed
    assert shown["n_revisions"] == 0
    assert shown["new_unit"] is False
    assert shown["workflow"] == "W17"

    assert assert_event(capfire, events.SHAPING_REPLY_COMPLETED)["has_proposal"] is True


@pytest.mark.anyio
@pytest.mark.workflow("W18")
async def test_a_revision_proposal_is_tagged_w18(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None, capfire
) -> None:
    """TDD §9's "W18 revision fields", on the surface that produces them."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)

        await _send(client, _send_url(path_id), _body(_ask(FORCE_PROPOSAL_REVISE)))

    shown = assert_event(capfire, events.PROPOSAL_SHOWN)
    assert (shown["n_add_lessons"], shown["n_revisions"]) == (0, 1)
    assert shown["workflow"] == "W18"


# --------------------------------------------------------------------------- #
# Apply & Undo (W17 / W18 / W19)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W17")
async def test_applying_an_addition_emits_the_change_with_the_ids_it_created(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None, capfire
) -> None:
    """``lesson_ids`` is the yield metric's join key — the rows apply really wrote.

    Asserted against the database rather than against the payload: the payload
    names titles and a position, and the *ids* only exist once the insert has
    happened. Getting this wrong would leave the primary metric joining on
    nothing and reading a permanent zero.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, before = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id, proposal=_proposal(_addition())
        )

        response = await client.post(_apply_url(message_id))

    assert response.status_code == 200, response.text
    change_id = response.json()["change"]["id"]
    created = [
        lesson_id
        for lesson_id in await _lesson_ids(path_id)
        if lesson_id not in {str(existing) for existing in before}
    ]

    applied = assert_event(capfire, events.CHANGE_APPLIED)
    assert applied["account_id"] == str(user_id)
    assert applied["path_id"] == str(path_id)
    assert applied["change_id"] == change_id
    assert applied["n_add_lessons"] == 2
    assert applied["n_revisions"] == 0
    assert applied["new_unit"] is False
    assert applied["workflow"] == "W17"
    # Logfire carries a list attribute as JSON **text**, which is the shape the
    # saved queries unnest (``(attributes ->> 'lesson_ids')::jsonb``) and the
    # shape test_metrics_replay's fixture reproduces. Asserting on the decoded
    # value here is what pins that this is really a JSON array and not, say, a
    # Python ``repr`` that no SQL cast could read.
    assert sorted(json.loads(applied["lesson_ids"])) == sorted(created)


@pytest.mark.anyio
@pytest.mark.workflow("W18")
async def test_applying_a_revision_emits_its_target_id_and_w18(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None, capfire
) -> None:
    """A Revision creates no row, so its ``lesson_ids`` is the lesson it re-teaches."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lessons = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(_revision(lesson_id=lessons[-1]), summary="Revises."),
        )

        response = await client.post(_apply_url(message_id))

    assert response.status_code == 200, response.text

    applied = assert_event(capfire, events.CHANGE_APPLIED)
    assert applied["n_add_lessons"] == 0
    assert applied["n_revisions"] == 1
    assert json.loads(applied["lesson_ids"]) == [str(lessons[-1])]
    assert applied["workflow"] == "W18"


@pytest.mark.anyio
async def test_an_addition_bringing_a_new_unit_says_so(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None, capfire
) -> None:
    """``new_unit`` is the "a new topic, not more of the same" signal."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(
                _addition(
                    insert_at_position=4,
                    new_unit={"title": "Lifetimes", "summary": "Borrowing over time."},
                )
            ),
        )

        response = await client.post(_apply_url(message_id))

    assert response.status_code == 200, response.text
    assert assert_event(capfire, events.CHANGE_APPLIED)["new_unit"] is True


@pytest.mark.anyio
async def test_a_refused_apply_emits_nothing(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None, capfire
) -> None:
    """A ``409`` changed nothing, so it must not appear in the applied count.

    Double-tapping is the ordinary case (§5.8), and a second ``change_applied``
    would inflate both proposal acceptance and the yield denominator.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id, proposal=_proposal(_addition())
        )

        assert (await client.post(_apply_url(message_id))).status_code == 200
        second = await client.post(_apply_url(message_id))

    assert second.status_code == 409, second.text
    assert len(captured_records(capfire, events.CHANGE_APPLIED)) == 1


@pytest.mark.anyio
@pytest.mark.workflow("W19")
async def test_undoing_a_change_emits_its_regret_latency(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None, capfire
) -> None:
    """``minutes_since_apply`` is fractional, so an immediate undo is not zero-ed.

    An undo seconds after the apply is the most interesting one there is; whole
    minutes would file every one of them as ``0`` and flatten the distribution
    the §7 time-to-undo reading exists to see.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id, proposal=_proposal(_addition())
        )

        applied = await client.post(_apply_url(message_id))
        assert applied.status_code == 200, applied.text
        change_id = applied.json()["change"]["id"]

        undone = await client.post(_undo_url(change_id))

    assert undone.status_code == 204, undone.text

    event = assert_event(capfire, events.CHANGE_UNDONE)
    assert event["account_id"] == str(user_id)
    assert event["path_id"] == str(path_id)
    assert event["change_id"] == change_id
    assert event["workflow"] == "W19"
    assert isinstance(event["minutes_since_apply"], float)
    assert 0.0 <= event["minutes_since_apply"] < 1.0


@pytest.mark.anyio
async def test_a_refused_undo_emits_nothing(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None, capfire
) -> None:
    """Undoing twice is a ``409``; the undo rate counts state changes only."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id, proposal=_proposal(_addition())
        )

        applied = await client.post(_apply_url(message_id))
        change_id = applied.json()["change"]["id"]
        assert (await client.post(_undo_url(change_id))).status_code == 204
        second = await client.post(_undo_url(change_id))

    assert second.status_code == 409, second.text
    assert len(captured_records(capfire, events.CHANGE_UNDONE)) == 1


@pytest.mark.anyio
@pytest.mark.workflow("W21")
async def test_the_shaping_events_never_reach_the_in_lesson_thread(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None, capfire
) -> None:
    """A shaping turn emits shaping events and **no** tutor events (W21).

    The two rails share a semaphore, a timeout and an error vocabulary; they must
    not share a metric. A ``tutor_message_sent`` from the shaping rail would put
    shaping traffic inside 2A's adoption number and inside its primary metric.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)

        await _send(client, _send_url(path_id), _body())
        # A read of the shaping thread must not emit anything either.
        await client.get(_conversation_url(path_id))

    assert captured_records(capfire, events.TUTOR_MESSAGE_SENT) == []
    assert captured_records(capfire, events.TUTOR_CONVERSATION_STARTED) == []
    assert captured_records(capfire, events.TUTOR_REPLY_COMPLETED) == []
    assert len(captured_records(capfire, events.SHAPING_MESSAGE_SENT)) == 1


@pytest.mark.anyio
async def test_the_change_id_on_the_event_is_the_row_that_landed(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None, capfire
) -> None:
    """The event's ``change_id`` addresses a real ``path_changes`` row.

    Cheap, and it is what makes the undo rate a *join* rather than two unrelated
    counts.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id, proposal=_proposal(_addition())
        )
        assert (await client.post(_apply_url(message_id))).status_code == 200

    applied = assert_event(capfire, events.CHANGE_APPLIED)
    assert applied["change_id"] == str(await _change_id(path_id))
