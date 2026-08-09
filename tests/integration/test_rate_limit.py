"""Integration tests for the daily rate limiter against real Postgres (AL-042).

The unit tests pin the cap/rollover/admin logic with a fake counter; these pin
the *counting* — that ``UsageRepository`` counts the right real rows (learner
filter, the UTC-day window on ``created_at`` / ``generation_started_at``) so the
service seam enforces caps end-to-end against the database.

Service-seam scope: POST /paths does not exist yet (AL-050 depends on this
ticket), so enforcement is exercised at the ``DailyRateLimiter`` seam over rows
inserted directly, not through an HTTP route. AL-050 carries the route wiring +
its 429 endpoint contract test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from fastapi import HTTPException
from sqlalchemy import update

from aleph import db
from aleph.config import Settings
from aleph.models import (
    Beat,
    BeatResearchRun,
    BeatResearchState,
    ConversationKind,
    Lesson,
    LessonGenerationState,
    Level,
    Message,
    MessageSource,
    Path,
    Unit,
)
from aleph.repositories import BeatRepository, ConversationRepository, UsageRepository
from aleph.services.rate_limit import DailyRateLimiter, build_daily_rate_limiter

from .conftest import create_user

if TYPE_CHECKING:
    import uuid

# A stable "now" so the UTC-day window is deterministic regardless of wall clock.
NOW = datetime(2026, 7, 23, 15, 0, tzinfo=UTC)
YESTERDAY = NOW - timedelta(days=1)


def _limiter(
    session,
    *,
    paths: int = 10,
    lessons: int = 100,
    tutor_messages: int = 0,
    brief_research: int = 0,
) -> DailyRateLimiter:
    return DailyRateLimiter(
        UsageRepository(session),
        paths_per_day=paths,
        lesson_generations_per_day=lessons,
        tutor_messages_per_day=tutor_messages,
        brief_research_per_day=brief_research,
        now=lambda: NOW,
    )


async def _make_path(
    session, *, user_id: uuid.UUID, created_at: datetime | None = None
) -> Path:
    path = Path(user_id=user_id, topic="Rust ownership", level=Level.SOME_EXPERIENCE)
    session.add(path)
    await session.flush()
    if created_at is not None:
        # created_at has a server default, so backdate it explicitly to place the
        # row outside today's window.
        await session.execute(
            update(Path).where(Path.id == path.id).values(created_at=created_at)
        )
    return path


async def _make_lesson(
    session,
    *,
    path: Path,
    unit: Unit,
    position: int,
    generation_started_at: datetime | None,
) -> Lesson:
    lesson = Lesson(
        unit=unit,
        path=path,
        position_in_path=position,
        position_in_unit=position,
        title=f"Lesson {position}",
        generation_state=(
            LessonGenerationState.GENERATING
            if generation_started_at is not None
            else LessonGenerationState.UNGENERATED
        ),
        generation_started_at=generation_started_at,
    )
    session.add(lesson)
    await session.flush()
    return lesson


@pytest.mark.anyio
async def test_path_cap_counts_real_rows_created_today() -> None:
    async with db.async_session() as session:
        user = await create_user(session, username="capped", subject="capped")
        limiter = _limiter(session, paths=10)

        # Creations 1..10 each pass, then the row lands (real INSERT).
        for _ in range(10):
            await limiter.check_path_creation(user_id=user.id, is_admin=False)
            await _make_path(session, user_id=user.id)

        # The 11th check counts 10 real rows today and denies.
        with pytest.raises(HTTPException) as excinfo:
            await limiter.check_path_creation(user_id=user.id, is_admin=False)
        assert excinfo.value.status_code == 429


@pytest.mark.anyio
async def test_path_cap_only_counts_todays_rows() -> None:
    async with db.async_session() as session:
        user = await create_user(session, username="rollover", subject="rollover")
        # Ten paths, but all backdated to yesterday: outside today's UTC window.
        for _ in range(10):
            await _make_path(session, user_id=user.id, created_at=YESTERDAY)

        # Fresh allowance today despite ten rows existing.
        await _limiter(session, paths=10).check_path_creation(
            user_id=user.id, is_admin=False
        )


@pytest.mark.anyio
async def test_path_cap_is_per_account() -> None:
    async with db.async_session() as session:
        capped = await create_user(session, username="a", subject="a")
        other = await create_user(session, username="b", subject="b")
        for _ in range(10):
            await _make_path(session, user_id=capped.id)

        limiter = _limiter(session, paths=10)
        with pytest.raises(HTTPException):
            await limiter.check_path_creation(user_id=capped.id, is_admin=False)
        # The other account's rows are not counted against ``capped``.
        await limiter.check_path_creation(user_id=other.id, is_admin=False)


@pytest.mark.anyio
async def test_admin_is_exempt_over_real_rows() -> None:
    async with db.async_session() as session:
        user = await create_user(session, username="admin", subject="admin")
        for _ in range(10):
            await _make_path(session, user_id=user.id)
        # Over the cap, but admin: no raise.
        await _limiter(session, paths=10).check_path_creation(
            user_id=user.id, is_admin=True
        )


@pytest.mark.anyio
async def test_outline_cap_counts_paths_with_attempt_today() -> None:
    """The retry cap counts ``paths`` by ``generation_started_at`` (the claim
    stamp), not ``created_at`` — so it bounds outline attempts, whether from a
    create or a retry, within the UTC day."""
    async with db.async_session() as session:
        user = await create_user(session, username="outline", subject="outline")

        # Two paths with an outline attempt today, one yesterday, one never.
        for _ in range(2):
            path = await _make_path(session, user_id=user.id)
            await session.execute(
                update(Path).where(Path.id == path.id).values(generation_started_at=NOW)
            )
        old = await _make_path(session, user_id=user.id)
        await session.execute(
            update(Path)
            .where(Path.id == old.id)
            .values(generation_started_at=YESTERDAY)
        )
        await _make_path(session, user_id=user.id)  # never attempted (NULL stamp)
        await session.flush()

        # Cap of 2 → the two attempted today put the learner at the cap; deny.
        with pytest.raises(HTTPException) as excinfo:
            await _limiter(session, paths=2).check_outline_generation(
                user_id=user.id, is_admin=False
            )
        assert excinfo.value.status_code == 429

        # Cap of 3 → only two count today (yesterday's + never-attempted excluded);
        # under the cap, allowed.
        await _limiter(session, paths=3).check_outline_generation(
            user_id=user.id, is_admin=False
        )


@pytest.mark.anyio
async def test_lesson_generation_cap_counts_rows_started_today() -> None:
    async with db.async_session() as session:
        user = await create_user(session, username="lessons", subject="lessons")
        path = await _make_path(session, user_id=user.id)
        unit = Unit(path=path, position=1, title="Unit 1", summary="s")
        session.add(unit)
        await session.flush()

        # Two lessons triggered today, one triggered yesterday, one never.
        await _make_lesson(
            session, path=path, unit=unit, position=1, generation_started_at=NOW
        )
        await _make_lesson(
            session, path=path, unit=unit, position=2, generation_started_at=NOW
        )
        await _make_lesson(
            session, path=path, unit=unit, position=3, generation_started_at=YESTERDAY
        )
        await _make_lesson(
            session, path=path, unit=unit, position=4, generation_started_at=None
        )

        # Cap of 2 → the two started today put the learner at the cap; deny.
        with pytest.raises(HTTPException) as excinfo:
            await _limiter(session, lessons=2).check_lesson_generation(
                user_id=user.id, is_admin=False
            )
        assert excinfo.value.status_code == 429

        # Cap of 3 → only two count today (yesterday's + ungenerated excluded);
        # under the cap, allowed.
        await _limiter(session, lessons=3).check_lesson_generation(
            user_id=user.id, is_admin=False
        )


@pytest.mark.anyio
async def test_lesson_cap_is_per_account() -> None:
    """The Path.user_id join is the only learner scoping — pin it."""
    async with db.async_session() as session:
        capped = await create_user(session, username="lc-a", subject="lc-a")
        other = await create_user(session, username="lc-b", subject="lc-b")
        path = await _make_path(session, user_id=capped.id)
        unit = Unit(path=path, position=1, title="Unit 1", summary="s")
        session.add(unit)
        await session.flush()
        await _make_lesson(
            session, path=path, unit=unit, position=1, generation_started_at=NOW
        )
        await _make_lesson(
            session, path=path, unit=unit, position=2, generation_started_at=NOW
        )

        limiter = _limiter(session, lessons=2)
        with pytest.raises(HTTPException):
            await limiter.check_lesson_generation(user_id=capped.id, is_admin=False)
        # The other account's lessons are not counted against ``other``.
        await limiter.check_lesson_generation(user_id=other.id, is_admin=False)


# --------------------------------------------------------------------------- #
# The tutor message cap (AL-220, Phase 2 §7 / D8)
# --------------------------------------------------------------------------- #


async def _make_turn(
    session,
    *,
    path: Path,
    lesson: Lesson,
    created_at: datetime | None = None,
) -> None:
    """Commit one whole turn (learner + tutor rows) onto ``path``'s thread."""
    repository = ConversationRepository(session)
    conversation, _created = await repository.upsert_for_path(
        path.id, kind=ConversationKind.LESSON
    )
    learner, tutor = await repository.insert_turn(
        conversation_id=conversation.id,
        lesson_id=lesson.id,
        learner_content="Why does a move invalidate the source?",
        source=MessageSource.TYPED,
        tutor_content="Because ownership is unique.",
    )
    if created_at is not None:
        # ``created_at`` has a server default, so backdate both rows explicitly
        # to place the turn outside today's window.
        await session.execute(
            update(Message)
            .where(Message.id.in_([learner.id, tutor.id]))
            .values(created_at=created_at)
        )


@pytest.mark.anyio
async def test_tutor_cap_counts_live_learner_rows_today() -> None:
    """Learner rows only, today only — the tutor row never double-counts a turn."""
    async with db.async_session() as session:
        user = await create_user(session, username="tutor-cap", subject="tutor-cap")
        path = await _make_path(session, user_id=user.id)
        unit = Unit(path=path, position=1, title="Unit 1", summary="s")
        session.add(unit)
        await session.flush()
        lesson = await _make_lesson(
            session, path=path, unit=unit, position=1, generation_started_at=NOW
        )

        # Two turns today, one yesterday: four learner+tutor rows in today's
        # window would trip a cap of 2 if the tutor rows counted, and three
        # would if yesterday's did. Only the two live learner rows count.
        await _make_turn(session, path=path, lesson=lesson)
        await _make_turn(session, path=path, lesson=lesson)
        await _make_turn(session, path=path, lesson=lesson, created_at=YESTERDAY)

        with pytest.raises(HTTPException) as excinfo:
            await _limiter(session, tutor_messages=2).check_tutor_message(
                user_id=user.id, is_admin=False
            )
        assert excinfo.value.status_code == 429

        await _limiter(session, tutor_messages=3).check_tutor_message(
            user_id=user.id, is_admin=False
        )


@pytest.mark.anyio
async def test_tutor_cap_is_per_account_and_admin_exempt() -> None:
    async with db.async_session() as session:
        capped = await create_user(session, username="tc-a", subject="tc-a")
        other = await create_user(session, username="tc-b", subject="tc-b")
        path = await _make_path(session, user_id=capped.id)
        unit = Unit(path=path, position=1, title="Unit 1", summary="s")
        session.add(unit)
        await session.flush()
        lesson = await _make_lesson(
            session, path=path, unit=unit, position=1, generation_started_at=NOW
        )
        await _make_turn(session, path=path, lesson=lesson)

        limiter = _limiter(session, tutor_messages=1)
        with pytest.raises(HTTPException):
            await limiter.check_tutor_message(user_id=capped.id, is_admin=False)
        await limiter.check_tutor_message(user_id=capped.id, is_admin=True)
        await limiter.check_tutor_message(user_id=other.id, is_admin=False)


@pytest.mark.anyio
async def test_clearing_the_thread_refunds_tutor_quota() -> None:
    """The recorded D8 quirk, pinned as behaviour rather than left to prose.

    "New conversation" deletes the conversation and cascades its messages, so
    the live-row count drops and quota comes back. This is the precondition the
    refund-proof usage table exists to remove — while the cap ships at 0 it can
    never be observed, and this test is what keeps the trade-off honest if
    anyone ever raises the knob.
    """
    async with db.async_session() as session:
        user = await create_user(session, username="tc-refund", subject="tc-refund")
        path = await _make_path(session, user_id=user.id)
        unit = Unit(path=path, position=1, title="Unit 1", summary="s")
        session.add(unit)
        await session.flush()
        lesson = await _make_lesson(
            session, path=path, unit=unit, position=1, generation_started_at=NOW
        )
        await _make_turn(session, path=path, lesson=lesson)

        limiter = _limiter(session, tutor_messages=1)
        with pytest.raises(HTTPException):
            await limiter.check_tutor_message(user_id=user.id, is_admin=False)

        await ConversationRepository(session).delete_for_path(
            path.id, kind=ConversationKind.LESSON
        )
        await session.flush()

        await limiter.check_tutor_message(user_id=user.id, is_admin=False)


# --------------------------------------------------------------------------- #
# The Beat research cap (AL-521, Phase 6 TDD D14) —
# ``count_brief_research_runs_since``, counted over the append-only
# ``beat_research_runs`` table (one row per WON claim), never ``beats`` rows.
#
# **Code-review FIX 2.** The counter this section used to pin counted
# ``beats`` rows whose ``research_started_at`` fell today — but a claim
# OVERWRITES that single stamp on every (re-)claim, so the count could never
# exceed the learner's *Beat* count, which ``MAX_BEATS_PER_LEARNER`` bounds
# at 3 — strictly below ``RATE_LIMIT_BRIEF_RESEARCH_PER_DAY``'s default of 5.
# The cap could never fire at production settings. See
# ``models/beat_research_run.py`` for the full write-up.
# --------------------------------------------------------------------------- #


async def _make_beat(session, *, user_id: uuid.UUID) -> Beat:
    beat = Beat(
        user_id=user_id,
        topic="EU AI regulation",
        level=Level.SOME_EXPERIENCE,
        anchor_weekday=0,
    )
    session.add(beat)
    await session.flush()
    return beat


async def _make_research_run(
    session, *, beat_id: uuid.UUID, user_id: uuid.UUID, started_at: datetime
) -> None:
    """One row per WON claim (FIX 2) — the cap counts THIS table, never
    ``beats`` rows, because a claim overwrites ``beats.research_started_at``
    on every (re-)claim and a learner's Beat count sits well below the daily
    cap at production settings."""
    session.add(
        BeatResearchRun(beat_id=beat_id, user_id=user_id, started_at=started_at)
    )
    await session.flush()


@pytest.mark.anyio
async def test_brief_research_cap_counts_runs_not_beats() -> None:
    """FIX 2's own regression pin: three runs on the SAME Beat today (an
    initial claim plus two retries, say) must count as THREE, not one."""
    async with db.async_session() as session:
        user = await create_user(session, username="research", subject="research")
        beat = await _make_beat(session, user_id=user.id)

        # Three runs today, all on the SAME Beat, plus one from yesterday
        # (outside the window).
        await _make_research_run(
            session, beat_id=beat.id, user_id=user.id, started_at=NOW
        )
        await _make_research_run(
            session, beat_id=beat.id, user_id=user.id, started_at=NOW
        )
        await _make_research_run(
            session, beat_id=beat.id, user_id=user.id, started_at=NOW
        )
        await _make_research_run(
            session, beat_id=beat.id, user_id=user.id, started_at=YESTERDAY
        )

        # Cap of 3 -> the three runs today put the learner at the cap, even
        # though every one of them belongs to the SAME single Beat — the
        # exact shape the old (Beat-counting) counter could never reach.
        assert (
            await _limiter(session, brief_research=3).brief_research_capacity_available(
                user_id=user.id, is_admin=False
            )
            is False
        )

        # Cap of 4 -> still under (yesterday's excluded).
        assert (
            await _limiter(session, brief_research=4).brief_research_capacity_available(
                user_id=user.id, is_admin=False
            )
            is True
        )


@pytest.mark.anyio
async def test_brief_research_cap_never_raises_over_real_rows() -> None:
    """The load-bearing property (TDD §7): a real-row cap hit degrades to a
    plain ``False``, never an ``HTTPException`` — unlike every other cap in
    this module."""
    async with db.async_session() as session:
        user = await create_user(session, username="research-2", subject="research-2")
        beat = await _make_beat(session, user_id=user.id)
        for _ in range(5):
            await _make_research_run(
                session, beat_id=beat.id, user_id=user.id, started_at=NOW
            )

        result = await _limiter(
            session, brief_research=1
        ).brief_research_capacity_available(user_id=user.id, is_admin=False)
        assert result is False  # no exception raised to get here


@pytest.mark.anyio
async def test_brief_research_cap_is_per_account() -> None:
    """The run row's own ``user_id`` scoping is the only learner filter — pin
    it."""
    async with db.async_session() as session:
        capped = await create_user(session, username="br-a", subject="br-a")
        other = await create_user(session, username="br-b", subject="br-b")
        capped_beat = await _make_beat(session, user_id=capped.id)
        await _make_research_run(
            session, beat_id=capped_beat.id, user_id=capped.id, started_at=NOW
        )

        limiter = _limiter(session, brief_research=1)
        assert (
            await limiter.brief_research_capacity_available(
                user_id=capped.id, is_admin=False
            )
            is False
        )
        assert (
            await limiter.brief_research_capacity_available(
                user_id=capped.id, is_admin=True
            )
            is True
        )
        assert (
            await limiter.brief_research_capacity_available(
                user_id=other.id, is_admin=False
            )
            is True
        )


@pytest.mark.anyio
async def test_brief_research_cap_is_reachable_at_production_defaults() -> None:
    """FIX 2's own acceptance test (mandated by the code review): at
    PRODUCTION defaults (``MAX_BEATS_PER_LEARNER = 3``,
    ``RATE_LIMIT_BRIEF_RESEARCH_PER_DAY = 5``), a learner's Beat COUNT
    (bounded at 3) is strictly less than the daily cap (5) — so a counter
    keyed on Beats, not runs, could never reach it. Drives the REAL claim
    path (``BeatRepository.claim_research`` / ``claim_research_for_retry``)
    across the maximum three Beats to prove the cap is genuinely reachable:
    one claim each on two Beats plus a claim-and-two-retries on the third
    (1 + 1 + 3 = 5 runs, on only 3 Beats) exhausts it."""
    config = Settings()
    assert config.max_beats_per_learner == 3
    assert config.rate_limit_brief_research_per_day == 5

    async with db.async_session() as session:
        user = await create_user(session, username="prod-cap", subject="prod-cap")
        beat_ids = [
            (await _make_beat(session, user_id=user.id)).id
            for _ in range(config.max_beats_per_learner)
        ]
        await session.commit()

    async def _claim_then_mark_failed(beat_id: uuid.UUID, *, retry: bool) -> None:
        async with db.async_session() as session:
            repo = BeatRepository(session)
            claim = repo.claim_research_for_retry if retry else repo.claim_research
            fence = await claim(beat_id)
            assert fence is not None, "expected the claim to win"
            # Reset to `failed` so the NEXT call can re-claim via retry —
            # mirrors a run that failed and was retried by the learner.
            await session.execute(
                update(Beat)
                .where(Beat.id == beat_id)
                .values(research_state=BeatResearchState.FAILED)
            )
            await session.commit()

    await _claim_then_mark_failed(beat_ids[0], retry=False)
    await _claim_then_mark_failed(beat_ids[1], retry=False)
    await _claim_then_mark_failed(beat_ids[2], retry=False)
    await _claim_then_mark_failed(beat_ids[2], retry=True)
    await _claim_then_mark_failed(beat_ids[2], retry=True)

    async with db.async_session() as session:
        limiter = build_daily_rate_limiter(session, config=config)
        available = await limiter.brief_research_capacity_available(
            user_id=user.id, is_admin=False
        )
    assert available is False, (
        "5 research runs today across only 3 Beats did not exhaust the "
        "production RATE_LIMIT_BRIEF_RESEARCH_PER_DAY=5 cap — the counter is "
        "still bounded by Beat count, not run count."
    )
