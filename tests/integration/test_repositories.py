"""Repository integration tests against a real per-test Postgres database.

The load-bearing behaviour AL-011 must guarantee (TDD §5.4 / §6):

* **Atomic claim** — two concurrent claims on the same row yield exactly one
  winner (real concurrent sessions, not mocks).
* **Stale recovery** — a ``generating`` row older than ``GENERATION_STALE_AFTER``
  is re-claimable; a fresh one is not.
* **Failed is retry-only** — the auto claim never re-claims a ``failed`` row;
  only the explicit retry claim does (never silently retry-burns spend, §5.4).
* **Reads treat stale-``generating`` as failed** (§5.4/§6).
* **Progress summary** — per-path lesson-state counts the paths API needs (§6).

Claim/stale logic is SQL evaluated against the database clock, so it is tested
here against real Postgres (fakes over mocks) rather than as pure unit tests.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from aleph import db
from aleph.models import (
    Attempt,
    Lesson,
    LessonGenerationState,
    Level,
    Path,
    PathStatus,
    Unit,
)
from aleph.repositories import (
    AttemptRepository,
    LessonRepository,
    PathRepository,
    QuickCheckRepository,
    UnitRepository,
)

from .conftest import create_user, wait_until_lock_waiters

STALE_AGE = timedelta(minutes=4)  # > GENERATION_STALE_AFTER (3 min, §14)
FRESH_AGE = timedelta(minutes=1)  # < stale window


async def _make_path(
    session,
    *,
    user_id,
    status: PathStatus = PathStatus.PENDING,
    started_at: datetime | None = None,
    topic: str = "Rust ownership",
) -> Path:
    path = Path(
        user_id=user_id,
        topic=topic,
        level=Level.SOME_EXPERIENCE,
        status=status,
        generation_started_at=started_at,
    )
    session.add(path)
    await session.flush()
    return path


async def _make_lesson(
    session,
    *,
    path: Path,
    unit: Unit,
    position: int,
    state: LessonGenerationState = LessonGenerationState.UNGENERATED,
    started_at: datetime | None = None,
    completed: bool = False,
) -> Lesson:
    lesson = Lesson(
        unit=unit,
        path=path,
        position_in_path=position,
        position_in_unit=position,
        title=f"Lesson {position}",
        generation_state=state,
        generation_started_at=started_at,
        read_passage="body" if state is LessonGenerationState.GENERATED else None,
        generated_at=datetime.now(UTC)
        if state is LessonGenerationState.GENERATED
        else None,
        completed_at=datetime.now(UTC) if completed else None,
    )
    session.add(lesson)
    await session.flush()
    return lesson


async def _make_unit(session, *, path: Path, position: int = 1) -> Unit:
    unit = Unit(path=path, position=position, title=f"Unit {position}", summary="s")
    session.add(unit)
    await session.flush()
    return unit


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_path_repository_crud_round_trip() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        repo = PathRepository(session)
        path = await repo.create(
            user_id=user.id, topic="US healthcare", level=Level.NEW_TO_IT
        )
        await session.commit()
        path_id = path.id
        user_id = user.id

    async with db.async_session() as session:
        repo = PathRepository(session)
        fetched = await repo.get(path_id)
        assert fetched is not None
        assert fetched.topic == "US healthcare"
        assert fetched.status is PathStatus.PENDING  # Python-side default applied

        # Ownership scoping.
        assert await repo.get_for_user(path_id=path_id, user_id=user_id) is not None
        other = await create_user(session, username="other", subject="other-subj")
        assert await repo.get_for_user(path_id=path_id, user_id=other.id) is None

        listed = await repo.list_for_user(user_id=user_id)
        assert [p.id for p in listed] == [path_id]


@pytest.mark.anyio
async def test_path_repository_create_persists_guidance() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        repo = PathRepository(session)
        path = await repo.create(
            user_id=user.id,
            topic="US healthcare",
            level=Level.NEW_TO_IT,
            guidance="Focus on the payer side, skip provider billing.",
        )
        await session.commit()
        path_id = path.id

    async with db.async_session() as session:
        fetched = await PathRepository(session).get(path_id)
        assert fetched is not None
        assert fetched.guidance == "Focus on the payer side, skip provider billing."
        assert fetched.title is None  # never set at create; falls back to topic


@pytest.mark.anyio
async def test_path_repository_set_title_renames_and_needs_a_refresh() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = await PathRepository(session).create(
            user_id=user.id, topic="US healthcare", level=Level.NEW_TO_IT
        )
        await session.commit()
        path_id = path.id

    async with db.async_session() as session:
        repo = PathRepository(session)
        # Load an ORM instance BEFORE the rename, exactly as the PATCH route's
        # ``OwnedPath`` dependency does — this is the regression the route's
        # ``session.refresh`` guards against.
        loaded = await repo.get(path_id)
        assert loaded is not None

        await repo.set_title(path_id, title="US healthcare, payer side")
        await session.commit()

        await session.refresh(loaded)
        assert loaded.title == "US healthcare, payer side"
        assert loaded.display_title == "US healthcare, payer side"

        # Durable: a fresh read agrees, and ``topic`` is untouched.
        reloaded = await repo.get(path_id)
        assert reloaded is not None
        assert reloaded.title == "US healthcare, payer side"
        assert reloaded.topic == "US healthcare"


@pytest.mark.anyio
async def test_path_delete_cascades_tree() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id)
        unit = await _make_unit(session, path=path)
        await _make_lesson(session, path=path, unit=unit, position=1)
        await session.commit()
        path_id = path.id

    async with db.async_session() as session:
        deleted = await PathRepository(session).delete(path_id)
        await session.commit()
        assert deleted is True

    async with db.async_session() as session:
        assert await PathRepository(session).get(path_id) is None
        assert (await session.execute(select(Unit))).first() is None
        assert (await session.execute(select(Lesson))).first() is None

    async with db.async_session() as session:
        # Deleting a non-existent path reports no rows removed.
        assert await PathRepository(session).delete(path_id) is False


@pytest.mark.anyio
async def test_unit_and_lesson_repository_create_and_list() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id)
        unit = await UnitRepository(session).create(
            path_id=path.id, position=1, title="Foundations", summary="basics"
        )
        lrepo = LessonRepository(session)
        # Deliberately create out of order to prove ordered reads.
        await lrepo.create(
            unit_id=unit.id,
            path_id=path.id,
            position_in_path=2,
            position_in_unit=2,
            title="Second",
        )
        await lrepo.create(
            unit_id=unit.id,
            path_id=path.id,
            position_in_path=1,
            position_in_unit=1,
            title="First",
        )
        await session.commit()

        units = await UnitRepository(session).list_for_path(path.id)
        assert [u.title for u in units] == ["Foundations"]

        lessons = await lrepo.list_for_path(path.id)
        assert [lesson.position_in_path for lesson in lessons] == [1, 2]
        assert [lesson.title for lesson in lessons] == ["First", "Second"]


@pytest.mark.anyio
async def test_quick_check_and_attempt_first_wins() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id)
        unit = await _make_unit(session, path=path)
        lesson = await _make_lesson(
            session,
            path=path,
            unit=unit,
            position=1,
            state=LessonGenerationState.GENERATED,
        )
        qc = await QuickCheckRepository(session).create(
            lesson_id=lesson.id,
            stem="What owns a value?",
            options=["A variable", "The heap", "The compiler"],
            correct_index=0,
            explanation="One owning variable.",
        )
        await session.commit()
        qc_id = qc.id
        user_id = user.id

    async with db.async_session() as session:
        assert await QuickCheckRepository(session).get_for_lesson(lesson.id) is not None
        arepo = AttemptRepository(session)
        attempt, created = await arepo.record(
            quick_check_id=qc_id, user_id=user_id, selected_index=0, is_correct=True
        )
        await session.commit()
        assert created is True
        assert attempt.selected_index == 0

    async with db.async_session() as session:
        arepo = AttemptRepository(session)
        # A second answer must NOT overwrite the first (one Attempt of record).
        attempt2, created2 = await arepo.record(
            quick_check_id=qc_id, user_id=user_id, selected_index=1, is_correct=False
        )
        await session.commit()
        assert created2 is False
        assert attempt2.selected_index == 0  # first answer preserved
        assert attempt2.is_correct is True


@pytest.mark.anyio
async def test_concurrent_attempt_double_submit_records_exactly_one() -> None:
    """Two concurrent first-answers on the same Quick check: exactly one is
    recorded (``INSERT ... ON CONFLICT DO NOTHING`` is atomic under the unique
    ``(quick_check_id, user_id)`` index), the other reports ``created=False`` and
    reads back the winner — never a second row (§4 one-Attempt-of-record)."""
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.READY)
        unit = await _make_unit(session, path=path)
        lesson = await _make_lesson(
            session,
            path=path,
            unit=unit,
            position=1,
            state=LessonGenerationState.GENERATED,
        )
        qc = await QuickCheckRepository(session).create(
            lesson_id=lesson.id,
            stem="q",
            options=["a", "b", "c"],
            correct_index=0,
            explanation="e",
        )
        await session.commit()
        qc_id, user_id = qc.id, user.id

    async def submit(selected_index: int, *, is_correct: bool) -> bool:
        async with db.async_session() as session:
            _, created = await AttemptRepository(session).record(
                quick_check_id=qc_id,
                user_id=user_id,
                selected_index=selected_index,
                is_correct=is_correct,
            )
            await session.commit()
            return created

    results = await asyncio.gather(
        submit(0, is_correct=True), submit(1, is_correct=False)
    )
    assert results.count(True) == 1, results
    assert results.count(False) == 1, results

    async with db.async_session() as session:
        rows = (
            await session.execute(
                select(Attempt).where(
                    Attempt.quick_check_id == qc_id, Attempt.user_id == user_id
                )
            )
        ).scalars()
        assert len([*rows]) == 1  # exactly one Attempt of record


@pytest.mark.anyio
async def test_concurrent_complete_stamps_completed_at_exactly_once() -> None:
    """Two concurrent completes on the same available lesson: exactly one
    transitions it (``UPDATE ... WHERE completed_at IS NULL`` under the row lock),
    the other reports ``False`` and stamps nothing — so ``completed_at`` is set
    once and never re-stamped (documented idempotency, TN-4). Before the guard
    both writes re-stamped ``completed_at`` and both callers re-fired the window
    advance."""
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.READY)
        unit = await _make_unit(session, path=path)
        lesson = await _make_lesson(session, path=path, unit=unit, position=1)
        await session.commit()
        lesson_id = lesson.id

    async def complete() -> bool:
        async with db.async_session() as session:
            newly = await LessonRepository(session).mark_completed(lesson_id)
            await session.commit()
            return newly

    results = await asyncio.gather(complete(), complete())
    assert results.count(True) == 1, results  # exactly one transition
    assert results.count(False) == 1, results

    async with db.async_session() as session:
        lesson = await LessonRepository(session).get(lesson_id)
        assert lesson is not None
        stamped = lesson.completed_at
        assert stamped is not None

    # A later repeat is a no-op: returns False and does not re-stamp completed_at.
    async with db.async_session() as session:
        assert await LessonRepository(session).mark_completed(lesson_id) is False
        await session.commit()
    async with db.async_session() as session:
        lesson = await LessonRepository(session).get(lesson_id)
        assert lesson is not None
        assert lesson.completed_at == stamped  # unchanged after the second


# --------------------------------------------------------------------------- #
# Atomic claim — exactly one winner
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_two_concurrent_lesson_claims_exactly_one_winner() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.READY)
        unit = await _make_unit(session, path=path)
        lesson = await _make_lesson(session, path=path, unit=unit, position=1)
        await session.commit()
        lesson_id = lesson.id

    async def claim() -> bool:
        async with db.async_session() as session:
            won = await LessonRepository(session).claim_for_generation(lesson_id)
            await session.commit()
            return won is not None

    results = await asyncio.gather(claim(), claim())
    assert results.count(True) == 1, results
    assert results.count(False) == 1, results

    async with db.async_session() as session:
        lesson = await LessonRepository(session).get(lesson_id)
        assert lesson is not None
        assert lesson.generation_state is LessonGenerationState.GENERATING
        assert lesson.generation_started_at is not None


@pytest.mark.anyio
async def test_two_concurrent_path_claims_exactly_one_winner() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.PENDING)
        await session.commit()
        path_id = path.id

    async def claim() -> bool:
        async with db.async_session() as session:
            won = await PathRepository(session).claim_outline(path_id)
            await session.commit()
            return won is not None

    results = await asyncio.gather(claim(), claim())
    assert results.count(True) == 1, results
    assert results.count(False) == 1, results

    async with db.async_session() as session:
        path = await PathRepository(session).get(path_id)
        assert path is not None
        assert path.status is PathStatus.GENERATING
        assert path.generation_started_at is not None


@pytest.mark.anyio
async def test_lesson_claim_row_lock_forces_overlap_exactly_one_winner() -> None:
    """Force *true* row-lock contention rather than trusting ``gather`` to overlap.

    ``A`` claims but does not commit — it holds the row lock. ``B``'s claim then
    provably blocks on that lock (asserted via ``pg_stat_activity``, no timed
    sleep). Committing ``A`` releases ``B``, which re-evaluates the now-generating
    row and loses. A non-atomic (read-then-write) claim could let both win under
    this exact interleaving; the single guarded ``UPDATE`` cannot.
    """
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.READY)
        unit = await _make_unit(session, path=path)
        lesson = await _make_lesson(session, path=path, unit=unit, position=1)
        await session.commit()
        lesson_id = lesson.id

    async def claim_b() -> datetime | None:
        async with db.async_session() as session_b:
            won = await LessonRepository(session_b).claim_for_generation(lesson_id)
            await session_b.commit()
            return won

    async with db.async_session() as session_a:
        fence_a = await LessonRepository(session_a).claim_for_generation(lesson_id)
        assert fence_a is not None  # A holds the row lock, uncommitted

        b_task = asyncio.create_task(claim_b())
        # Deterministically wait until B is genuinely blocked on A's row lock.
        await asyncio.wait_for(wait_until_lock_waiters(1), timeout=10)
        # Release A; B unblocks, re-reads the fresh claim, and loses.
        await session_a.commit()
        fence_b = await b_task

    assert fence_b is None

    async with db.async_session() as session:
        lesson = await LessonRepository(session).get(lesson_id)
        assert lesson is not None
        assert lesson.generation_state is LessonGenerationState.GENERATING
        assert lesson.generation_started_at == fence_a  # A's claim stands


# --------------------------------------------------------------------------- #
# Stale recovery + failed-is-retry-only
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_lesson_claim_stale_reclaimable_fresh_not() -> None:
    now = datetime.now(UTC)
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.READY)
        unit = await _make_unit(session, path=path)
        stale = await _make_lesson(
            session,
            path=path,
            unit=unit,
            position=1,
            state=LessonGenerationState.GENERATING,
            started_at=now - STALE_AGE,
        )
        fresh = await _make_lesson(
            session,
            path=path,
            unit=unit,
            position=2,
            state=LessonGenerationState.GENERATING,
            started_at=now - FRESH_AGE,
        )
        await session.commit()
        stale_id, fresh_id = stale.id, fresh.id

    async with db.async_session() as session:
        repo = LessonRepository(session)
        assert await repo.claim_for_generation(stale_id) is not None
        assert await repo.claim_for_generation(fresh_id) is None
        await session.commit()


@pytest.mark.anyio
async def test_lesson_failed_reclaimable_only_via_retry() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.READY)
        unit = await _make_unit(session, path=path)
        failed = await _make_lesson(
            session,
            path=path,
            unit=unit,
            position=1,
            state=LessonGenerationState.FAILED,
        )
        await session.commit()
        failed_id = failed.id

    async with db.async_session() as session:
        # Auto claim (prefetch/reconciler) must NOT re-claim a real failure.
        assert await LessonRepository(session).claim_for_generation(failed_id) is None
        await session.rollback()

    async with db.async_session() as session:
        # Explicit learner retry re-claims it.
        assert await LessonRepository(session).claim_for_retry(failed_id) is not None
        await session.commit()

    async with db.async_session() as session:
        lesson = await LessonRepository(session).get(failed_id)
        assert lesson is not None
        assert lesson.generation_state is LessonGenerationState.GENERATING
        assert lesson.generation_error is None  # cleared on re-claim


@pytest.mark.anyio
async def test_lesson_generated_is_terminal_never_reclaimed() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.READY)
        unit = await _make_unit(session, path=path)
        done = await _make_lesson(
            session,
            path=path,
            unit=unit,
            position=1,
            state=LessonGenerationState.GENERATED,
        )
        await session.commit()
        done_id = done.id

    async with db.async_session() as session:
        repo = LessonRepository(session)
        assert await repo.claim_for_generation(done_id) is None
        assert await repo.claim_for_retry(done_id) is None
        await session.rollback()


@pytest.mark.anyio
async def test_path_failed_reclaimable_only_via_retry() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.FAILED)
        await session.commit()
        path_id = path.id

    async with db.async_session() as session:
        assert await PathRepository(session).claim_outline(path_id) is None
        await session.rollback()

    async with db.async_session() as session:
        assert (
            await PathRepository(session).claim_outline_for_retry(path_id) is not None
        )
        await session.commit()


@pytest.mark.anyio
async def test_path_claim_stale_reclaimable_fresh_not() -> None:
    now = datetime.now(UTC)
    async with db.async_session() as session:
        user = await create_user(session)
        stale = await _make_path(
            session,
            user_id=user.id,
            status=PathStatus.GENERATING,
            started_at=now - STALE_AGE,
        )
        fresh = await _make_path(
            session,
            user_id=user.id,
            status=PathStatus.GENERATING,
            started_at=now - FRESH_AGE,
            topic="another",
        )
        await session.commit()
        stale_id, fresh_id = stale.id, fresh.id

    async with db.async_session() as session:
        repo = PathRepository(session)
        assert await repo.claim_outline(stale_id) is not None
        assert await repo.claim_outline(fresh_id) is None
        await session.commit()


@pytest.mark.anyio
async def test_refused_path_is_terminal_never_reclaimed() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.REFUSED)
        await session.commit()
        path_id = path.id

    async with db.async_session() as session:
        repo = PathRepository(session)
        assert await repo.claim_outline(path_id) is None
        assert await repo.claim_outline_for_retry(path_id) is None
        await session.rollback()


# --------------------------------------------------------------------------- #
# Transitions
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_lesson_mark_transitions() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.READY)
        unit = await _make_unit(session, path=path)
        lesson = await _make_lesson(session, path=path, unit=unit, position=1)
        await session.commit()
        lesson_id = lesson.id
        repo = LessonRepository(session)
        fence = await repo.claim_for_generation(lesson_id)
        assert fence is not None
        assert (
            await repo.mark_generated(
                lesson_id=lesson_id, read_passage="Ownership is...", fence=fence
            )
            is True
        )
        await session.commit()

    # Read back in a fresh session: a Core UPDATE does not refresh the mutating
    # session's identity map (TDD §5.4 has tasks use short-lived sessions/step).
    async with db.async_session() as session:
        refreshed = await LessonRepository(session).get(lesson_id)
        assert refreshed is not None
        assert refreshed.generation_state is LessonGenerationState.GENERATED
        assert refreshed.read_passage == "Ownership is..."
        assert refreshed.generated_at is not None
        await LessonRepository(session).mark_completed(lesson_id)
        await session.commit()

    async with db.async_session() as session:
        refreshed = await LessonRepository(session).get(lesson_id)
        assert refreshed is not None
        assert refreshed.completed_at is not None


@pytest.mark.anyio
async def test_lesson_mark_failed_records_error() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.READY)
        unit = await _make_unit(session, path=path)
        lesson = await _make_lesson(session, path=path, unit=unit, position=1)
        await session.commit()
        lesson_id = lesson.id
        repo = LessonRepository(session)
        fence = await repo.claim_for_generation(lesson_id)
        assert fence is not None
        assert (
            await repo.mark_failed(
                lesson_id=lesson_id, error="model timeout", fence=fence
            )
            is True
        )
        await session.commit()

    async with db.async_session() as session:
        refreshed = await LessonRepository(session).get(lesson_id)
        assert refreshed is not None
        assert refreshed.generation_state is LessonGenerationState.FAILED
        assert refreshed.generation_error == "model timeout"


@pytest.mark.anyio
async def test_path_mark_transitions() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.PENDING)
        await session.commit()
        ready_id = path.id
        repo = PathRepository(session)
        fence = await repo.claim_outline(ready_id)
        assert fence is not None
        assert await repo.mark_ready(ready_id, fence=fence) is True
        await session.commit()

    async with db.async_session() as session:
        ready = await PathRepository(session).get(ready_id)
        assert ready is not None
        assert ready.status is PathStatus.READY

    async with db.async_session() as session:
        user = await create_user(session, username="b", subject="b")
        path = await _make_path(session, user_id=user.id, status=PathStatus.PENDING)
        await session.commit()
        refused_id = path.id
        repo = PathRepository(session)
        fence = await repo.claim_outline(refused_id)
        assert fence is not None
        assert (
            await repo.mark_refused(
                path_id=refused_id, message="Cannot help with that.", fence=fence
            )
            is True
        )
        await session.commit()

    async with db.async_session() as session:
        refreshed = await PathRepository(session).get(refused_id)
        assert refreshed is not None
        assert refreshed.status is PathStatus.REFUSED
        assert refreshed.refusal_message == "Cannot help with that."


# --------------------------------------------------------------------------- #
# Fenced marks — a late mark after a re-claim is a no-op (C1)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_lesson_late_mark_after_reclaim_is_noop() -> None:
    """A stalled worker that lost its claim to a stale re-claim must not write.

    Worker A claims (fence A). A stalls until its claim is stale; the reconciler
    re-claims (fence B), so the row is ``generating`` under B. A's late
    ``mark_generated`` with the now-defunct fence A must be a no-op — the row
    stays under B's fresh claim, uncorrupted (TDD §5.4 / §4 immutability).
    """
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.READY)
        unit = await _make_unit(session, path=path)
        lesson = await _make_lesson(session, path=path, unit=unit, position=1)
        await session.commit()
        lesson_id = lesson.id

    # Worker A claims.
    async with db.async_session() as session:
        fence_a = await LessonRepository(session).claim_for_generation(lesson_id)
        await session.commit()
    assert fence_a is not None

    # A stalls: age its claim past the stale window so it becomes re-claimable.
    async with db.async_session() as session:
        await session.execute(
            update(Lesson)
            .where(Lesson.id == lesson_id)
            .values(generation_started_at=datetime.now(UTC) - STALE_AGE)
        )
        await session.commit()

    # Reconciler re-claims (fence B).
    async with db.async_session() as session:
        fence_b = await LessonRepository(session).claim_for_generation(lesson_id)
        await session.commit()
    assert fence_b is not None
    assert fence_b != fence_a

    # A finally returns and tries to record its (now stale) result.
    async with db.async_session() as session:
        lost = await LessonRepository(session).mark_generated(
            lesson_id=lesson_id, read_passage="stale content", fence=fence_a
        )
        await session.commit()
    assert lost is False

    async with db.async_session() as session:
        refreshed = await LessonRepository(session).get(lesson_id)
        assert refreshed is not None
        # Still under B's fresh claim — A's write was dropped.
        assert refreshed.generation_state is LessonGenerationState.GENERATING
        assert refreshed.read_passage is None
        assert refreshed.generation_started_at == fence_b


@pytest.mark.anyio
async def test_lesson_mark_after_terminal_is_noop() -> None:
    """``generated`` is terminal: a later ``mark_failed`` (even with the winning
    fence) must not flip it (§4 content immutable). The state guard blocks it —
    the fence alone would not, since ``generation_started_at`` persists."""
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.READY)
        unit = await _make_unit(session, path=path)
        lesson = await _make_lesson(session, path=path, unit=unit, position=1)
        await session.commit()
        lesson_id = lesson.id
        repo = LessonRepository(session)
        fence = await repo.claim_for_generation(lesson_id)
        assert fence is not None
        assert (
            await repo.mark_generated(
                lesson_id=lesson_id, read_passage="done", fence=fence
            )
            is True
        )
        # Same fence, but the row is now terminal: the mark must not apply.
        assert (
            await repo.mark_failed(lesson_id=lesson_id, error="late", fence=fence)
            is False
        )
        await session.commit()

    async with db.async_session() as session:
        refreshed = await LessonRepository(session).get(lesson_id)
        assert refreshed is not None
        assert refreshed.generation_state is LessonGenerationState.GENERATED
        assert refreshed.generation_error is None


@pytest.mark.anyio
async def test_path_late_mark_after_reclaim_is_noop() -> None:
    """Path outline: a stalled outline task's late ``mark_ready`` after a stale
    re-claim is a no-op (same fencing as lessons, §5.4/§5.5)."""
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.PENDING)
        await session.commit()
        path_id = path.id

    async with db.async_session() as session:
        fence_a = await PathRepository(session).claim_outline(path_id)
        await session.commit()
    assert fence_a is not None

    async with db.async_session() as session:
        await session.execute(
            update(Path)
            .where(Path.id == path_id)
            .values(generation_started_at=datetime.now(UTC) - STALE_AGE)
        )
        await session.commit()

    async with db.async_session() as session:
        fence_b = await PathRepository(session).claim_outline(path_id)
        await session.commit()
    assert fence_b is not None
    assert fence_b != fence_a

    async with db.async_session() as session:
        lost = await PathRepository(session).mark_ready(path_id, fence=fence_a)
        await session.commit()
    assert lost is False

    async with db.async_session() as session:
        refreshed = await PathRepository(session).get(path_id)
        assert refreshed is not None
        assert refreshed.status is PathStatus.GENERATING  # still under B's claim
        assert refreshed.generation_started_at == fence_b


# --------------------------------------------------------------------------- #
# Reads treat stale-generating as failed (§5.4/§6)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_effective_state_buckets_stale_generating_as_failed() -> None:
    now = datetime.now(UTC)
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.READY)
        unit = await _make_unit(session, path=path)
        stale = await _make_lesson(
            session,
            path=path,
            unit=unit,
            position=1,
            state=LessonGenerationState.GENERATING,
            started_at=now - STALE_AGE,
        )
        fresh = await _make_lesson(
            session,
            path=path,
            unit=unit,
            position=2,
            state=LessonGenerationState.GENERATING,
            started_at=now - FRESH_AGE,
        )
        done = await _make_lesson(
            session,
            path=path,
            unit=unit,
            position=3,
            state=LessonGenerationState.GENERATED,
        )
        await session.commit()
        repo = LessonRepository(session)
        assert await repo.effective_state(stale.id) is LessonGenerationState.FAILED
        assert await repo.effective_state(fresh.id) is LessonGenerationState.GENERATING
        assert await repo.effective_state(done.id) is LessonGenerationState.GENERATED
        assert await repo.effective_state(uuid.uuid4()) is None


@pytest.mark.anyio
async def test_list_for_path_with_effective_state_bulk_read() -> None:
    """The §6 poll target needs each lesson's effective state; one query returns
    all of them in total order (no per-id N-query fan-out), stale ``generating``
    bucketed as ``failed``."""
    now = datetime.now(UTC)
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.READY)
        unit = await _make_unit(session, path=path)
        await _make_lesson(
            session,
            path=path,
            unit=unit,
            position=1,
            state=LessonGenerationState.GENERATING,
            started_at=now - STALE_AGE,
        )
        await _make_lesson(
            session,
            path=path,
            unit=unit,
            position=2,
            state=LessonGenerationState.GENERATING,
            started_at=now - FRESH_AGE,
        )
        await _make_lesson(
            session,
            path=path,
            unit=unit,
            position=3,
            state=LessonGenerationState.GENERATED,
        )
        await _make_lesson(session, path=path, unit=unit, position=4)
        await session.commit()
        path_id = path.id

    async with db.async_session() as session:
        rows = await LessonRepository(session).list_for_path_with_effective_state(
            path_id
        )
        assert [lesson.position_in_path for lesson, _ in rows] == [1, 2, 3, 4]
        assert [state for _, state in rows] == [
            LessonGenerationState.FAILED,  # stale generating
            LessonGenerationState.GENERATING,  # fresh
            LessonGenerationState.GENERATED,
            LessonGenerationState.UNGENERATED,
        ]


@pytest.mark.anyio
async def test_path_effective_status_buckets_stale_generating_as_failed() -> None:
    """A path whose outline run crashed (stale ``generating``) reads as
    ``failed`` so the learner gets a retry, not a dead spinner (§5.4/§6)."""
    now = datetime.now(UTC)
    async with db.async_session() as session:
        user = await create_user(session)
        stale = await _make_path(
            session,
            user_id=user.id,
            status=PathStatus.GENERATING,
            started_at=now - STALE_AGE,
        )
        fresh = await _make_path(
            session,
            user_id=user.id,
            status=PathStatus.GENERATING,
            started_at=now - FRESH_AGE,
            topic="fresh",
        )
        ready = await _make_path(
            session, user_id=user.id, status=PathStatus.READY, topic="ready"
        )
        await session.commit()
        stale_id, fresh_id, ready_id = stale.id, fresh.id, ready.id

    async with db.async_session() as session:
        repo = PathRepository(session)
        assert await repo.effective_status(stale_id) is PathStatus.FAILED
        assert await repo.effective_status(fresh_id) is PathStatus.GENERATING
        assert await repo.effective_status(ready_id) is PathStatus.READY
        assert await repo.effective_status(uuid.uuid4()) is None


@pytest.mark.anyio
async def test_progress_summary_counts_by_effective_state() -> None:
    now = datetime.now(UTC)
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.READY)
        unit = await _make_unit(session, path=path)
        # position 1: generated + completed
        await _make_lesson(
            session,
            path=path,
            unit=unit,
            position=1,
            state=LessonGenerationState.GENERATED,
            completed=True,
        )
        # position 2: generated (not completed)
        await _make_lesson(
            session,
            path=path,
            unit=unit,
            position=2,
            state=LessonGenerationState.GENERATED,
        )
        # position 3: stale generating -> counts as failed
        await _make_lesson(
            session,
            path=path,
            unit=unit,
            position=3,
            state=LessonGenerationState.GENERATING,
            started_at=now - STALE_AGE,
        )
        # position 4: fresh generating
        await _make_lesson(
            session,
            path=path,
            unit=unit,
            position=4,
            state=LessonGenerationState.GENERATING,
            started_at=now - FRESH_AGE,
        )
        # position 5: ungenerated
        await _make_lesson(session, path=path, unit=unit, position=5)
        await session.commit()
        path_id = path.id

    async with db.async_session() as session:
        summaries = await LessonRepository(session).progress_summaries([path_id])
        progress = summaries[path_id]
        assert progress.total_lessons == 5
        assert progress.completed_lessons == 1
        assert progress.generated_lessons == 2
        assert progress.by_state[LessonGenerationState.GENERATED] == 2
        assert progress.by_state[LessonGenerationState.FAILED] == 1  # stale bucketed
        assert progress.by_state[LessonGenerationState.GENERATING] == 1  # fresh only
        assert progress.by_state[LessonGenerationState.UNGENERATED] == 1


@pytest.mark.anyio
async def test_progress_summaries_zero_for_path_without_lessons() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.PENDING)
        await session.commit()
        path_id = path.id

    async with db.async_session() as session:
        summaries = await LessonRepository(session).progress_summaries([path_id])
        progress = summaries[path_id]
        assert progress.total_lessons == 0
        assert progress.completed_lessons == 0
        assert progress.generated_lessons == 0
        assert all(count == 0 for count in progress.by_state.values())


# --------------------------------------------------------------------------- #
# Continuity + progression reads
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_first_incomplete_returns_lowest_position_open_lesson() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.READY)
        unit = await _make_unit(session, path=path)
        await _make_lesson(
            session,
            path=path,
            unit=unit,
            position=1,
            state=LessonGenerationState.GENERATED,
            completed=True,
        )
        second = await _make_lesson(
            session,
            path=path,
            unit=unit,
            position=2,
            state=LessonGenerationState.GENERATED,
        )
        await _make_lesson(session, path=path, unit=unit, position=3)
        await session.commit()
        second_id = second.id
        path_id = path.id

    async with db.async_session() as session:
        first_open = await LessonRepository(session).first_incomplete(path_id)
        assert first_open is not None
        assert first_open.id == second_id


@pytest.mark.anyio
async def test_generated_passages_before_returns_ordered_continuity_context() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user_id=user.id, status=PathStatus.READY)
        unit = await _make_unit(session, path=path)
        for position in (1, 2, 3):
            lesson = Lesson(
                unit=unit,
                path=path,
                position_in_path=position,
                position_in_unit=position,
                title=f"L{position}",
                generation_state=LessonGenerationState.GENERATED,
                read_passage=f"passage {position}",
                generated_at=datetime.now(UTC),
            )
            session.add(lesson)
        # An ungenerated lesson at position 4 has no passage yet.
        await _make_lesson(session, path=path, unit=unit, position=4)
        await session.commit()
        path_id = path.id

    async with db.async_session() as session:
        # Continuity context for lesson at position 3 = passages 1 and 2, in
        # order, each carrying its real (unit_title, lesson_title) locator.
        prior = await LessonRepository(session).generated_passages_before(
            path_id=path_id, position_in_path=3
        )
        assert list(prior) == [
            ("Unit 1", "L1", "passage 1"),
            ("Unit 1", "L2", "passage 2"),
        ]
