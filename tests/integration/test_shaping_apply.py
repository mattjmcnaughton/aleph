"""Apply, Undo and the Change history, end to end (AL-321, TDD §5.6-§5.8, §11).

The phase's correctness heart, against real Postgres and the real HTTP surface.
Everything here is the TDD §11 integration matrix:

* **Apply** inserts the rows the payload describes — right positions, right unit
  grouping, ``ungenerated`` — and the untouched Phase 1 pipeline then generates
  them; a Revision snapshots, resets, regenerates *with* its instruction, and
  clears it.
* **The stale matrix** (D5): target attempted since / positions shifted by an
  earlier Change / cap now exceeded / target generating → four distinct ``409``
  reasons the card can render, and **zero mutation** in every one of them.
* **Undo** restores byte-identical state — asserted as full-table equality
  against a snapshot taken before the apply, because "restores exactly" (PRD
  §5.5) is a transactional claim and a spot check would not test it.
* **The engagement boundary is the rule** (D2), re-checked server-side at undo;
  the UI's disabled button is a convenience.
* **The per-path lock** (D11): two concurrent applies of one Proposal, one wins.

Rows are seeded directly and Proposal payloads are written by hand: apply reads
a *stored* payload, so the honest arrange is to store one — and building them
explicitly is what lets the stale cases exist at all (a payload the shaper would
draft against live state is by construction not stale).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select

from aleph import db
from aleph.config import settings
from aleph.models import (
    Attempt,
    Lesson,
    LessonGenerationState,
    PathChange,
    PathChangeKind,
    PathChangeStatus,
    QuickCheck,
    Unit,
)
from aleph.repositories import ChangeRepository
from aleph.services import generation as gen_module
from aleph.services import shaping as shaping_service
from aleph.services.shaping import PathApplyLock
from aleph.services.stub_model import (
    REVISED_PASSAGE_MARKER,
    SHAPING_REVISION_INSTRUCTION,
)

from ._shaping_send_harness import (
    LESSON_COUNT,
    OTHER,
    OWNER,
    _client,
    _conversation_url,
    _seed_path,
    _seed_shaping_turn,
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
from .conftest import CollectingSpawn, stub_resolver

if TYPE_CHECKING:
    from fastapi import FastAPI


# --------------------------------------------------------------------------- #
# Fixtures & helpers
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def spawn(monkeypatch: pytest.MonkeyPatch) -> CollectingSpawn:
    """The generation singleton's seams: the stub model + a drainable spawn.

    Autouse because **apply answers with the refreshed path**, which goes through
    the same read seam ``GET /paths/{id}`` uses — and that seam is a *trigger*
    (poll-as-trigger, §5.4). A test that forgot this would spawn a real
    generation against a real provider just by applying a proposal. Draining the
    collector is also how the "and then Phase 1 generates it" half of W17/W18 is
    asserted deterministically.
    """
    collector = CollectingSpawn()
    monkeypatch.setattr(
        gen_module.generation_orchestrator, "_resolve_model", stub_resolver()
    )
    monkeypatch.setattr(gen_module.generation_orchestrator, "_spawn", collector)
    return collector


@pytest.fixture(autouse=True)
def isolated_apply_locks(monkeypatch: pytest.MonkeyPatch) -> PathApplyLock:
    """A fresh per-path lock registry per test.

    The service singleton owns one for the process, and an ``asyncio.Lock`` binds
    to the loop it is first awaited on — sharing it across tests would bind it to
    a dead one. Same reason the reply limiter is isolated.
    """
    locks = PathApplyLock()
    monkeypatch.setattr(shaping_service.shaping_change_service, "_locks", locks)
    return locks


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


def _revision(
    *,
    lesson_id: uuid.UUID,
    instruction: str = SHAPING_REVISION_INSTRUCTION,
    new_title: str | None = None,
) -> dict[str, Any]:
    return {
        "lesson_id": str(lesson_id),
        "instruction": instruction,
        "rationale": "You have not started this lesson yet.",
        "new_title": new_title,
    }


def _proposal(*operations: dict[str, Any], summary: str = "Adds lessons.") -> Any:
    return {"operations": list(operations), "summary": summary}


def _apply_url(message_id: uuid.UUID | str) -> str:
    return f"/api/v1/messages/{message_id}/apply-proposal"


def _undo_url(change_id: uuid.UUID | str) -> str:
    return f"/api/v1/changes/{change_id}/undo"


def _changes_url(path_id: uuid.UUID | str) -> str:
    return f"/api/v1/paths/{path_id}/changes"


async def _lessons(path_id: uuid.UUID) -> list[Lesson]:
    async with db.async_session() as session:
        result = await session.execute(
            select(Lesson)
            .where(Lesson.path_id == path_id)
            .order_by(Lesson.position_in_path)
        )
        return list(result.scalars())


async def _units(path_id: uuid.UUID) -> list[Unit]:
    async with db.async_session() as session:
        result = await session.execute(
            select(Unit).where(Unit.path_id == path_id).order_by(Unit.position)
        )
        return list(result.scalars())


async def _snapshot(path_id: uuid.UUID) -> dict[str, Any]:
    """Every column undo promises to restore, for full-table equality (§11).

    Audit stamps are excluded on purpose — ``updated_at`` moves whenever a row is
    written and undo *does* write these rows. What must come back identical is
    the learner-visible and progression-relevant state: the order, the grouping,
    the titles, the content and its generation axis, and the Quick check.
    """
    lessons = await _lessons(path_id)
    units = await _units(path_id)
    async with db.async_session() as session:
        checks = list(
            (
                await session.execute(
                    select(QuickCheck)
                    .join(Lesson, QuickCheck.lesson_id == Lesson.id)
                    .where(Lesson.path_id == path_id)
                    .order_by(Lesson.position_in_path)
                )
            ).scalars()
        )
    return {
        "lessons": [
            (
                lesson.id,
                lesson.unit_id,
                lesson.position_in_path,
                lesson.position_in_unit,
                lesson.title,
                lesson.generation_state,
                lesson.read_passage,
                lesson.generated_at,
                lesson.completed_at,
                lesson.revision_instruction,
            )
            for lesson in lessons
        ],
        "units": [(unit.id, unit.position, unit.title, unit.summary) for unit in units],
        "quick_checks": [
            (
                check.lesson_id,
                check.stem,
                list(check.options),
                check.correct_index,
                check.explanation,
            )
            for check in checks
        ],
    }


async def _attempt(*, lesson_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Record an Attempt on a lesson's Quick check — the D2 engagement signal."""
    async with db.async_session() as session:
        check = (
            await session.execute(
                select(QuickCheck).where(QuickCheck.lesson_id == lesson_id)
            )
        ).scalar_one()
        session.add(
            Attempt(
                quick_check_id=check.id,
                user_id=user_id,
                selected_index=0,
                is_correct=False,
            )
        )
        await session.commit()


async def _seed_recorded_change(*, path_id: uuid.UUID, insert_at: int) -> None:
    """An applied Change from *after* the Proposal — recorded, not enacted.

    The arrange for "positions shifted by an earlier change". What the apply-time
    freshness check reads is the Change **history**, so the row is what has to
    exist; leaving the live path untouched is deliberate, because that keeps the
    pending Proposal valid against the shared predicates. This is exactly the
    case the check exists for — a payload that is still well formed and no longer
    means what the learner was shown.
    """
    async with db.async_session() as session:
        await ChangeRepository(session).create(
            path_id=path_id,
            message_id=None,
            kind=PathChangeKind.ADD_LESSONS,
            payload={
                "operations": [_addition(insert_at_position=insert_at)],
                "summary": "An earlier change moved everything down.",
            },
        )
        await session.commit()


# --------------------------------------------------------------------------- #
# Apply: the happy paths (W17, W18)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W17")
async def test_applying_an_addition_inserts_ungenerated_rows_in_place(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """The payload's positions become the path's, and the rest shifts down (D6)."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id, proposal=_proposal(_addition(insert_at_position=2))
        )

        response = await client.post(_apply_url(message_id))

    assert response.status_code == 200, response.text
    lessons = await _lessons(path_id)
    assert [lesson.title for lesson in lessons] == [
        "Ownership, part 1",
        "Borrowing in practice",
        "Lifetimes, gently",
        "Ownership, part 2",
        "Ownership, part 3",
    ]
    assert [lesson.position_in_path for lesson in lessons] == [1, 2, 3, 4, 5]
    # The added rows are ordinary ``ungenerated`` lessons — Phase 1's machinery
    # writes their content (CONTEXT.md: *Addition*).
    added = lessons[1:3]
    assert all(
        lesson.generation_state is LessonGenerationState.UNGENERATED for lesson in added
    )
    # They joined the unit that owned the position they name, keeping a unit's
    # lessons contiguous in the total order.
    assert {lesson.unit_id for lesson in lessons} == {lessons[0].unit_id}
    assert [lesson.position_in_unit for lesson in lessons] == [1, 2, 3, 4, 5]
    # Nothing that already existed changed identity.
    assert [lesson.id for lesson in lessons if lesson.id in set(lesson_ids)] == [
        lesson_ids[0],
        lesson_ids[1],
        lesson_ids[2],
    ]


@pytest.mark.anyio
async def test_apply_answers_with_the_change_and_the_refreshed_path(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """§5.6 step 4: ghosts swap for real rows in one round trip."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(
                _addition(insert_at_position=4, titles=("A tail lesson",)),
                summary="Adds 1 lesson at the end, about 5 minutes.",
            ),
        )

        response = await client.post(_apply_url(message_id))
        history = await client.get(_changes_url(path_id))

    body = response.json()
    assert body["change"]["kinds"] == ["add_lessons"]
    assert body["change"]["status"] == "applied"
    assert body["change"]["summary"] == "Adds 1 lesson at the end, about 5 minutes."
    assert body["change"]["undone_at"] is None
    titles = [
        lesson["title"] for unit in body["path"]["units"] for lesson in unit["lessons"]
    ]
    assert titles[-1] == "A tail lesson"
    assert body["path"]["id"] == str(path_id)
    assert history.json()["changes"][0]["id"] == body["change"]["id"]


@pytest.mark.anyio
@pytest.mark.workflow("W17")
async def test_added_lessons_generate_through_the_untouched_phase_1_pipeline(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    shaping_flag_enabled: None,
    spawn: CollectingSpawn,
) -> None:
    """A Change is applied when structure lands; generation follows (PRD §5.7)."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(_addition(insert_at_position=2, titles=("Inserted",))),
        )

        assert (await client.post(_apply_url(message_id))).status_code == 200
        await spawn.drain()

    inserted = next(
        lesson for lesson in await _lessons(path_id) if lesson.title == "Inserted"
    )
    assert inserted.generation_state is LessonGenerationState.GENERATED
    assert inserted.read_passage
    async with db.async_session() as session:
        check = (
            await session.execute(
                select(QuickCheck).where(QuickCheck.lesson_id == inserted.id)
            )
        ).scalar_one_or_none()
    assert check is not None, "the Phase 1 pipeline wrote its Quick check too"


@pytest.mark.anyio
async def test_an_addition_with_a_new_unit_lands_in_unit_order(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """A new unit's display position follows its lessons' place in the path."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(
                _addition(
                    insert_at_position=LESSON_COUNT + 1,
                    titles=("Error handling basics", "Error handling in anger"),
                    new_unit={
                        "title": "Error handling",
                        "summary": "Handling failure without panicking.",
                    },
                )
            ),
        )

        response = await client.post(_apply_url(message_id))

    assert response.status_code == 200, response.text
    units = await _units(path_id)
    assert [unit.title for unit in units] == ["Foundations", "Error handling"]
    assert [unit.position for unit in units] == [1, 2]
    lessons = await _lessons(path_id)
    assert [lesson.unit_id for lesson in lessons[-2:]] == [units[1].id] * 2
    assert [lesson.position_in_unit for lesson in lessons[-2:]] == [1, 2]


@pytest.mark.anyio
@pytest.mark.workflow("W18")
async def test_applying_a_revision_snapshots_resets_and_regenerates(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    shaping_flag_enabled: None,
    spawn: CollectingSpawn,
) -> None:
    """D7 end to end: snapshot, clear, regenerate with the instruction, clear it.

    The structural link W18 asserts on: the stub marks a regenerated passage when
    it sees its own ``SHAPING_REVISION_INSTRUCTION`` in the lesson prompt, so the
    marker below *is* the proof that apply's instruction reached generation — with
    no orchestration change anywhere.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(
                _revision(lesson_id=lesson_ids[0], new_title="Ownership, re-pitched"),
                summary="Re-teaches one lesson.",
            ),
        )

        assert (await client.post(_apply_url(message_id))).status_code == 200

        # Immediately after apply: reset, instruction set, Quick check gone.
        mid = (await _lessons(path_id))[0]
        assert mid.generation_state is LessonGenerationState.UNGENERATED
        assert mid.read_passage is None
        assert mid.generated_at is None
        assert mid.title == "Ownership, re-pitched"
        assert mid.revision_instruction == SHAPING_REVISION_INSTRUCTION
        assert mid.position_in_path == 1, "a Revision keeps the lesson's slot"
        async with db.async_session() as session:
            gone = (
                await session.execute(
                    select(QuickCheck).where(QuickCheck.lesson_id == mid.id)
                )
            ).scalar_one_or_none()
        assert gone is None

        await spawn.drain()

    after = (await _lessons(path_id))[0]
    assert after.generation_state is LessonGenerationState.GENERATED
    assert after.read_passage is not None
    assert REVISED_PASSAGE_MARKER in after.read_passage
    assert after.revision_instruction is None, "cleared on generated (D7)"


@pytest.mark.anyio
async def test_the_pre_revision_snapshot_lives_on_the_change_row(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """The row is self-sufficient for undo *and* feeds the revision prompt (D8/D7)."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(_revision(lesson_id=lesson_ids[0])),
        )
        await client.post(_apply_url(message_id))

    async with db.async_session() as session:
        change = (
            await session.execute(
                select(PathChange).where(PathChange.path_id == path_id)
            )
        ).scalar_one()
    snapshot = change.payload["inverse"]["revisions"][0]
    assert snapshot["lesson_id"] == str(lesson_ids[0])
    assert "Ownership is Rust's memory model." in snapshot["read_passage"]
    assert snapshot["quick_check"]["correct_index"] == 1
    assert change.payload["summary"]
    assert change.kind == "revise_lesson"


# --------------------------------------------------------------------------- #
# Apply: the stale matrix (D5, §5.8) — a distinct 409 code, and zero mutation
# --------------------------------------------------------------------------- #


async def _assert_unchanged(
    path_id: uuid.UUID, before: dict[str, Any], *, changes: int = 0
) -> None:
    """A refused apply touched nothing: not the path, not the history.

    ``changes`` is how many Change rows the *arrange* already put there — the
    freshness case seeds one deliberately — so the assertion stays "this apply
    recorded nothing" rather than "the table is empty".
    """
    assert await _snapshot(path_id) == before, "a refused apply mutated the path"
    async with db.async_session() as session:
        recorded = (
            await session.execute(
                select(PathChange).where(PathChange.path_id == path_id)
            )
        ).scalars()
        assert len(list(recorded)) == changes, "a refused apply recorded a Change"


@pytest.mark.anyio
async def test_a_revision_target_attempted_since_is_409_revision_target_engaged(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """The engagement boundary at apply time (D2/D5), not at draft time."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id, proposal=_proposal(_revision(lesson_id=lesson_ids[0]))
        )
        await _attempt(lesson_id=lesson_ids[0], user_id=user_id)
        before = await _snapshot(path_id)

        response = await client.post(_apply_url(message_id))

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "conflict"
    assert error["details"]["reason"] == "revision_target_engaged"
    assert error["message"], "the card renders a sentence, not a bare code"
    await _assert_unchanged(path_id, before)


@pytest.mark.anyio
async def test_positions_shifted_by_a_later_change_is_409_positions_shifted(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """A payload can stay well formed and stop meaning what the learner saw (D5).

    The recorded position is all an Addition has to name its slot with, so a
    Change applied *after* the Proposal that inserted at or before it moves that
    slot under the card. The predicates cannot see this — the payload is still
    in bounds — which is why apply re-resolves positions as a separate step.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id, proposal=_proposal(_addition(insert_at_position=3))
        )
        await _seed_recorded_change(path_id=path_id, insert_at=1)
        before = await _snapshot(path_id)

        response = await client.post(_apply_url(message_id))

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "positions_shifted"
    await _assert_unchanged(path_id, before, changes=1)


@pytest.mark.anyio
async def test_a_change_inserting_between_the_payloads_positions_is_409(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """The freshness bound is the payload's **last** insert point, not its first.

    One Proposal may carry several Additions, each naming its own slot. A later
    Change that lands *between* two of them leaves the first one meaning exactly
    what the learner saw and the second one meaning something else — so a check
    that bounds on the earliest insert point waves the whole payload through and
    the later Addition lands one position early, before a lesson it was drawn
    after.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(
                _addition(insert_at_position=2, titles=("A head lesson",)),
                _addition(insert_at_position=4, titles=("A tail lesson",)),
            ),
        )
        await _seed_recorded_change(path_id=path_id, insert_at=3)
        before = await _snapshot(path_id)

        response = await client.post(_apply_url(message_id))

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "positions_shifted"
    await _assert_unchanged(path_id, before, changes=1)


@pytest.mark.anyio
async def test_an_undo_after_the_proposal_is_a_shift_too(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    shaping_flag_enabled: None,
    spawn: CollectingSpawn,
) -> None:
    """An **Undo** moves positions exactly as the Apply it reverses did (D5).

    Apply a Change, draft a Proposal against the path that Change made, then undo
    it: every position at or after the undone Change's insert point moves *down*
    by its size, and the pending Proposal's recorded slot now names a different
    lesson. Only reading live Changes would miss it entirely — the row that
    shifted the path is precisely the one that is no longer in force.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)
        _l1, first_message = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(
                _addition(insert_at_position=2, titles=("First one", "First two"))
            ),
        )
        applied = await client.post(_apply_url(first_message))
        assert applied.status_code == 200, applied.text
        # Drafted against the five-lesson path: position 4 is "Ownership, part 2".
        _l2, pending_message = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(
                _addition(insert_at_position=4, titles=("A pending lesson",))
            ),
        )
        await spawn.drain()
        undone = await client.post(_undo_url(applied.json()["change"]["id"]))
        assert undone.status_code == 204, undone.text
        before = await _snapshot(path_id)

        response = await client.post(_apply_url(pending_message))

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "positions_shifted"
    await _assert_unchanged(path_id, before, changes=1)


@pytest.mark.anyio
async def test_a_title_that_now_collides_is_409_title_conflict(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """Phase 1's "a title never repeats in a path" survives Apply, not just draft."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(
                _addition(insert_at_position=2, titles=("Ownership, part 2",))
            ),
        )
        before = await _snapshot(path_id)

        response = await client.post(_apply_url(message_id))

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "title_conflict"
    await _assert_unchanged(path_id, before)


@pytest.mark.anyio
async def test_a_path_at_its_cap_is_409_path_cap_reached(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """``MAX_LESSONS_PER_PATH`` is checked at proposal *and* apply time (§7)."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id, proposal=_proposal(_addition(insert_at_position=2))
        )
        monkeypatch.setattr(settings, "max_lessons_per_path", LESSON_COUNT)
        before = await _snapshot(path_id)

        response = await client.post(_apply_url(message_id))

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "path_cap_reached"
    await _assert_unchanged(path_id, before)


@pytest.mark.anyio
async def test_a_generating_revision_target_is_409_target_generating(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """Retryable, not stale: a prefetch holds the claim and will let go (§5.6)."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id, proposal=_proposal(_revision(lesson_id=lesson_ids[0]))
        )
        async with db.async_session() as session:
            lesson = await session.get(Lesson, lesson_ids[0])
            assert lesson is not None
            lesson.generation_state = LessonGenerationState.GENERATING
            await session.commit()
        before = await _snapshot(path_id)

        response = await client.post(_apply_url(message_id))

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "target_generating"
    await _assert_unchanged(path_id, before)


@pytest.mark.anyio
async def test_an_insert_position_below_the_boundary_is_409(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """Nothing is ever inserted before work the learner has engaged with (D2)."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id, proposal=_proposal(_addition(insert_at_position=1))
        )
        await _attempt(lesson_id=lesson_ids[0], user_id=user_id)
        before = await _snapshot(path_id)

        response = await client.post(_apply_url(message_id))

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "insert_position_taken"
    await _assert_unchanged(path_id, before)


@pytest.mark.anyio
async def test_applying_the_same_proposal_twice_is_409_already_applied(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """A double tap is an idempotent-friendly refusal, not a second Change."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(_addition(insert_at_position=2, titles=("Once only",))),
        )

        first = await client.post(_apply_url(message_id))
        second = await client.post(_apply_url(message_id))

    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    assert second.json()["error"]["details"]["reason"] == "already_applied"
    assert [lesson.title for lesson in await _lessons(path_id)].count("Once only") == 1


@pytest.mark.anyio
async def test_concurrent_applies_of_one_proposal_leave_one_change(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """The per-path lock (D11): they serialize, and the loser sees the winner."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(
                _addition(insert_at_position=2, titles=("Exactly one",))
            ),
        )

        first, second = await asyncio.gather(
            client.post(_apply_url(message_id)),
            client.post(_apply_url(message_id)),
        )

    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [200, 409]
    loser = first if first.status_code == 409 else second
    assert loser.json()["error"]["details"]["reason"] == "already_applied"
    async with db.async_session() as session:
        changes = list(
            (
                await session.execute(
                    select(PathChange).where(PathChange.path_id == path_id)
                )
            ).scalars()
        )
    assert len(changes) == 1
    assert [lesson.title for lesson in await _lessons(path_id)].count(
        "Exactly one"
    ) == 1


@pytest.mark.anyio
async def test_the_database_refuses_a_second_apply_the_pre_check_missed(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    shaping_flag_enabled: None,
    spawn: CollectingSpawn,
) -> None:
    """The cross-process half of "applied at most once" (migration ``0007``).

    The apply lock and the pre-check inside it are **process-local**, and a Fly
    rolling deploy briefly runs two machines: two taps can land on different
    ones, each reading a history that does not yet hold the other's row. Blinding
    the pre-check is exactly that view, and what stops the second write is then
    the partial unique index — mapped back to the same ``409 already_applied``,
    with the whole transaction rolled back behind it.

    A Revision is the vehicle rather than an Addition because re-applying an
    Addition is refused by the title predicate long before it reaches the insert;
    a Revision of an already-revised lesson is still perfectly valid.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id, proposal=_proposal(_revision(lesson_id=lesson_ids[0]))
        )
        first = await client.post(_apply_url(message_id))
        assert first.status_code == 200, first.text
        # Apply kicked the prefetch driver; let it finish, or the second apply
        # is refused as ``target_generating`` before it can reach the insert.
        await spawn.drain()
        before = await _snapshot(path_id)

        async def _blind(self: ChangeRepository, message_id: uuid.UUID) -> None:
            return None

        monkeypatch.setattr(ChangeRepository, "resolution_of_message", _blind)
        second = await client.post(_apply_url(message_id))

    assert second.status_code == 409, second.text
    assert second.json()["error"]["details"]["reason"] == "already_applied"
    assert await _snapshot(path_id) == before, "the losing transaction rolled back"
    async with db.async_session() as session:
        changes = list(
            (
                await session.execute(
                    select(PathChange).where(PathChange.path_id == path_id)
                )
            ).scalars()
        )
    assert len(changes) == 1


# --------------------------------------------------------------------------- #
# Undo (§5.7, D8)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W19")
async def test_undoing_an_addition_restores_byte_identical_state(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    shaping_flag_enabled: None,
    spawn: CollectingSpawn,
) -> None:
    """ "Restores exactly" is a transactional claim, so assert the whole table.

    Two Additions in one Proposal, deliberately: that is the case where a lesson
    moves **twice**, so the unshift has to be reverse-*chronological* rather than
    merely ascending — something a single-insertion test cannot reach. Generation
    is drained first, so undo deletes fully ``generated`` rows rather than
    half-written ones.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(
                _addition(insert_at_position=2),
                _addition(insert_at_position=1, titles=("A head lesson",)),
                summary="Adds 3 lessons.",
            ),
        )
        before = await _snapshot(path_id)

        applied = await client.post(_apply_url(message_id))
        change_id = applied.json()["change"]["id"]
        await spawn.drain()
        assert [lesson.position_in_path for lesson in await _lessons(path_id)] == [
            1,
            2,
            3,
            4,
            5,
            6,
        ]

        undone = await client.post(_undo_url(change_id))

    assert undone.status_code == 204, undone.text
    assert await _snapshot(path_id) == before
    async with db.async_session() as session:
        change = await session.get(PathChange, uuid.UUID(change_id))
        assert change is not None, "undo is a status, never a delete"
        assert change.status is PathChangeStatus.UNDONE
        assert change.undone_at is not None


@pytest.mark.anyio
@pytest.mark.workflow("W19")
async def test_undoing_an_addition_with_a_new_unit_restores_unit_order(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    shaping_flag_enabled: None,
    spawn: CollectingSpawn,
) -> None:
    """The unit renumbering is reversed too, under its own unique constraint."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(
                _addition(
                    insert_at_position=1,
                    titles=("A prelude",),
                    new_unit={"title": "Before we start", "summary": "Orientation."},
                )
            ),
        )
        before = await _snapshot(path_id)

        applied = await client.post(_apply_url(message_id))
        await spawn.drain()
        assert [unit.title for unit in await _units(path_id)] == [
            "Before we start",
            "Foundations",
        ]

        undone = await client.post(_undo_url(applied.json()["change"]["id"]))

    assert undone.status_code == 204, undone.text
    assert await _snapshot(path_id) == before


@pytest.mark.anyio
@pytest.mark.workflow("W19")
async def test_undoing_a_revision_restores_the_passage_and_the_quick_check(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    shaping_flag_enabled: None,
    spawn: CollectingSpawn,
) -> None:
    """Restored from the Change's own snapshot — undo needs no second source (D8).

    Drained first, which is also the harder case: the lesson has been *rewritten*
    by the time undo runs, so the pre-revision passage and Quick check exist
    nowhere but the Change's payload.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(
                _revision(lesson_id=lesson_ids[0], new_title="A different pitch"),
                summary="Re-teaches one lesson.",
            ),
        )
        before = await _snapshot(path_id)

        applied = await client.post(_apply_url(message_id))
        await spawn.drain()
        undone = await client.post(_undo_url(applied.json()["change"]["id"]))

    assert undone.status_code == 204, undone.text
    assert await _snapshot(path_id) == before
    restored = (await _lessons(path_id))[0]
    assert restored.generation_state is LessonGenerationState.GENERATED
    assert restored.revision_instruction is None


@pytest.mark.anyio
@pytest.mark.workflow("W19")
async def test_undo_after_engaging_with_the_change_is_409_engaged(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    shaping_flag_enabled: None,
    spawn: CollectingSpawn,
) -> None:
    """The D2 re-check is the rule; the disabled button is the convenience."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(_addition(insert_at_position=2, titles=("Started it",))),
        )
        applied = await client.post(_apply_url(message_id))
        change_id = applied.json()["change"]["id"]
        await spawn.drain()  # Phase 1 gives the added lesson a Quick check
        added = next(
            lesson for lesson in await _lessons(path_id) if lesson.title == "Started it"
        )
        await _attempt(lesson_id=added.id, user_id=user_id)

        response = await client.post(_undo_url(change_id))

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "engaged"
    assert [lesson.title for lesson in await _lessons(path_id)].count("Started it") == 1
    async with db.async_session() as session:
        change = await session.get(PathChange, uuid.UUID(change_id))
        assert change is not None
        assert change.status is PathChangeStatus.APPLIED


@pytest.mark.anyio
async def test_undoing_twice_is_409_not_applied(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(_addition(insert_at_position=2, titles=("Undo me",))),
        )
        applied = await client.post(_apply_url(message_id))
        change_id = applied.json()["change"]["id"]

        first = await client.post(_undo_url(change_id))
        second = await client.post(_undo_url(change_id))

    assert first.status_code == 204, first.text
    assert second.status_code == 409, second.text
    assert second.json()["error"]["details"]["reason"] == "not_applied"


@pytest.mark.anyio
@pytest.mark.workflow("W19")
async def test_undoing_a_superseded_change_is_409_not_latest_when_slots_collide(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    shaping_flag_enabled: None,
    spawn: CollectingSpawn,
) -> None:
    """Undo is **LIFO**: a Change a later one has built on cannot be reversed.

    The inverse replays *absolute* positions recorded against the path as it was.
    Here the later Change inserted between the earlier one's two added lessons,
    so replaying the earlier inverse walks an existing lesson straight into the
    later Change's slot — ``UNIQUE (path_id, position_in_path)`` and a 500. The
    honest answer is to refuse: undo the later Change first.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)
        _l1, first_message = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(
                _addition(insert_at_position=2, titles=("First one", "First two"))
            ),
        )
        first = await client.post(_apply_url(first_message))
        assert first.status_code == 200, first.text
        _l2, second_message = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(
                _addition(insert_at_position=3, titles=("A wedge",)),
                summary="Wedged between the first change's lessons.",
            ),
        )
        second = await client.post(_apply_url(second_message))
        assert second.status_code == 200, second.text
        await spawn.drain()
        before = await _snapshot(path_id)

        response = await client.post(_undo_url(first.json()["change"]["id"]))

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "not_latest"
    assert response.json()["error"]["message"], "the sheet renders a sentence"
    assert await _snapshot(path_id) == before, "a refused undo mutated the path"
    async with db.async_session() as session:
        change = await session.get(PathChange, uuid.UUID(first.json()["change"]["id"]))
        assert change is not None
        assert change.status is PathChangeStatus.APPLIED


@pytest.mark.anyio
@pytest.mark.workflow("W19")
async def test_undoing_a_superseded_change_is_409_not_latest_when_order_would_drift(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    shaping_flag_enabled: None,
    spawn: CollectingSpawn,
) -> None:
    """The quiet half of the same hazard: no error, just an order nobody proposed.

    The later Change inserted *after* the earlier one's lessons, so replaying the
    earlier inverse collides with nothing — it simply unshifts two lessons past
    the later Change's lesson, which the learner had placed **before** them.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)
        _l1, first_message = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(
                _addition(insert_at_position=2, titles=("First one", "First two"))
            ),
        )
        first = await client.post(_apply_url(first_message))
        assert first.status_code == 200, first.text
        # Position 4 is "Ownership, part 2"; the learner asked for a lesson
        # *before* it, and undoing the first change must not move it after it.
        _l2, second_message = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(
                _addition(insert_at_position=4, titles=("A latecomer",))
            ),
        )
        second = await client.post(_apply_url(second_message))
        assert second.status_code == 200, second.text
        await spawn.drain()
        before = await _snapshot(path_id)

        response = await client.post(_undo_url(first.json()["change"]["id"]))

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "not_latest"
    assert await _snapshot(path_id) == before, "a refused undo mutated the path"


@pytest.mark.anyio
@pytest.mark.workflow("W19")
async def test_undo_in_reverse_order_walks_the_whole_stack_back(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    shaping_flag_enabled: None,
    spawn: CollectingSpawn,
) -> None:
    """LIFO is a restriction on *order*, not on reach: newest first undoes all.

    The same two Changes as the collision case, undone newest-first — each one is
    the newest live Change when its turn comes, and the path lands byte-identical
    to where it started.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)
        before = await _snapshot(path_id)
        _l1, first_message = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(
                _addition(insert_at_position=2, titles=("First one", "First two"))
            ),
        )
        first = await client.post(_apply_url(first_message))
        _l2, second_message = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(_addition(insert_at_position=3, titles=("A wedge",))),
        )
        second = await client.post(_apply_url(second_message))
        await spawn.drain()

        newest = await client.post(_undo_url(second.json()["change"]["id"]))
        assert newest.status_code == 204, newest.text
        oldest = await client.post(_undo_url(first.json()["change"]["id"]))

    assert oldest.status_code == 204, oldest.text
    assert await _snapshot(path_id) == before


@pytest.mark.anyio
async def test_an_undone_proposal_cannot_be_re_applied(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """Re-applying would resurrect an edit the learner deliberately took back."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(_addition(insert_at_position=2, titles=("Once",))),
        )
        applied = await client.post(_apply_url(message_id))
        await client.post(_undo_url(applied.json()["change"]["id"]))

        again = await client.post(_apply_url(message_id))

    assert again.status_code == 409, again.text
    assert again.json()["error"]["details"]["reason"] == "already_undone"


@pytest.mark.anyio
@pytest.mark.workflow("W21")
async def test_undo_never_touches_progress(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    shaping_flag_enabled: None,
    spawn: CollectingSpawn,
) -> None:
    """PRD §5.5: undo removes only what the Change created.

    A completed lesson elsewhere on the path — and its Attempt — must be exactly
    where they were on both sides of an apply/undo round trip. By the engagement
    rule undo cannot even reach the Change's own content once it is met, so this
    pins the *other* half: unrelated progress is not collateral.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_ids = await _seed_path(user_id)
        await _attempt(lesson_id=lesson_ids[0], user_id=user_id)
        async with db.async_session() as session:
            lesson = await session.get(Lesson, lesson_ids[0])
            assert lesson is not None
            lesson.completed_at = lesson.created_at
            await session.commit()
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(_addition(insert_at_position=3, titles=("Later on",))),
        )
        before = await _snapshot(path_id)

        applied = await client.post(_apply_url(message_id))
        undone = await client.post(_undo_url(applied.json()["change"]["id"]))

    assert undone.status_code == 204, undone.text
    assert await _snapshot(path_id) == before
    async with db.async_session() as session:
        attempts = list((await session.execute(select(Attempt))).scalars())
    assert len(attempts) == 1, "no Attempt was created or destroyed"


# --------------------------------------------------------------------------- #
# Change history (§6) & ownership
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_history_reads_newest_first_and_keeps_undone_changes(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    shaping_flag_enabled: None,
    spawn: CollectingSpawn,
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_ids = await _seed_path(user_id)
        _l1, first_message = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(
                _revision(lesson_id=lesson_ids[2]), summary="Re-teaches lesson three."
            ),
        )
        _l2, second_message = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(
                _addition(insert_at_position=4, titles=("A newer lesson",)),
                summary="Adds one lesson at the end.",
            ),
        )
        first = await client.post(_apply_url(first_message))
        second = await client.post(_apply_url(second_message))
        # Apply kicks the prefetch driver, so the revised lesson is being
        # rewritten right now; undoing into a live claim is the retryable
        # ``target_generating`` case (§5.7), not what this test is about.
        await spawn.drain()
        # The **newest** live Change: undo is LIFO (§5.7), and a history that
        # keeps undone rows is what this test is about.
        undone = await client.post(_undo_url(second.json()["change"]["id"]))
        assert undone.status_code == 204, undone.text

        body = (await client.get(_changes_url(path_id))).json()

    assert [change["id"] for change in body["changes"]] == [
        second.json()["change"]["id"],
        first.json()["change"]["id"],
    ]
    assert [change["status"] for change in body["changes"]] == ["undone", "applied"]
    assert [change["kinds"] for change in body["changes"]] == [
        ["add_lessons"],
        ["revise_lesson"],
    ]
    assert body["changes"][0]["summary"] == "Adds one lesson at the end."
    assert body["changes"][0]["undone_at"] is not None


@pytest.mark.anyio
async def test_a_path_with_no_changes_reads_as_an_empty_list(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)

        response = await client.get(_changes_url(path_id))

    assert response.status_code == 200, response.text
    assert response.json() == {"changes": []}


@pytest.mark.anyio
async def test_the_history_survives_new_conversation(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """ "New conversation" is not "undo everything" (PRD §5.8, D3)."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(
                _addition(insert_at_position=2, titles=("Kept",)),
                summary="Adds one lesson.",
            ),
        )
        await client.post(_apply_url(message_id))

        assert (await client.delete(_conversation_url(path_id))).status_code == 204
        body = (await client.get(_changes_url(path_id))).json()

    assert [change["summary"] for change in body["changes"]] == ["Adds one lesson."]
    assert [lesson.title for lesson in await _lessons(path_id)].count("Kept") == 1


@pytest.mark.anyio
async def test_applying_another_learners_proposal_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    async with _client(app) as owner_client:
        owner_id = await _sign_in(owner_client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(owner_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id, proposal=_proposal(_addition())
        )
        before = await _snapshot(path_id)

    async with _client(app) as other_client:
        await _sign_in(other_client, monkeypatch, OTHER)
        response = await other_client.post(_apply_url(message_id))

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
    await _assert_unchanged(path_id, before)


@pytest.mark.anyio
async def test_undoing_another_learners_change_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    async with _client(app) as owner_client:
        owner_id = await _sign_in(owner_client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(owner_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id,
            proposal=_proposal(_addition(insert_at_position=2, titles=("Theirs",))),
        )
        applied = await owner_client.post(_apply_url(message_id))
        change_id = applied.json()["change"]["id"]

    async with _client(app) as other_client:
        await _sign_in(other_client, monkeypatch, OTHER)
        response = await other_client.post(_undo_url(change_id))

    assert response.status_code == 404, response.text
    async with db.async_session() as session:
        change = await session.get(PathChange, uuid.UUID(change_id))
        assert change is not None
        assert change.status is PathChangeStatus.APPLIED


@pytest.mark.anyio
async def test_another_learners_history_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    async with _client(app) as owner_client:
        owner_id = await _sign_in(owner_client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(owner_id)

    async with _client(app) as other_client:
        await _sign_in(other_client, monkeypatch, OTHER)
        response = await other_client.get(_changes_url(path_id))

    assert response.status_code == 404, response.text


@pytest.mark.anyio
async def test_a_message_with_no_proposal_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """Three different facts, one answer — distinguishing them discloses one."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(path_id=path_id)

        response = await client.post(_apply_url(message_id))
        missing = await client.post(_apply_url(uuid.uuid4()))

    assert response.status_code == 404, response.text
    assert missing.status_code == 404


@pytest.mark.anyio
async def test_the_apply_and_undo_routes_are_behind_the_shaping_flag(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ships dark: with the flag off the whole surface does not exist (epic #114)."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_ids = await _seed_path(user_id)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id, proposal=_proposal(_addition())
        )

        applied = await client.post(_apply_url(message_id))
        history = await client.get(_changes_url(path_id))
        undone = await client.post(_undo_url(uuid.uuid4()))

    assert [applied.status_code, history.status_code, undone.status_code] == [
        404,
        404,
        404,
    ]
    assert [lesson.title for lesson in await _lessons(path_id)] == [
        f"Ownership, part {position}" for position in range(1, LESSON_COUNT + 1)
    ]
