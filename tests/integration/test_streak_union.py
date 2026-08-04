"""Integration tests for the streak union (Phase 3 TDD D11/§5.5), real Postgres.

Everything ``tests/unit/test_progress_read.py`` proves against fakes, here
against the real ``FlashcardRepository.review_days_for_user`` query composed
through the real ``load_progress_summary`` service entry point — the same
escalation ``test_progress_api.py`` makes for the day-boundary sign convention
(D3/§14 R1), because a Postgres expression is not provably the same as the
pure-Python formula (or the fake) until something actually runs it.

Written in the ``test_progress_api.py`` / ``test_flashcards_schema.py`` style:
bare accounts, a path + lesson seeded directly (no generation, no HTTP —
that machinery is Phase 5's own and not what this file is testing), reviews
appended through ``FlashcardRepository.append_review_and_project`` the same
way ``test_flashcards_schema.py`` does, and the service called directly
(``load_progress_summary``) so ``flashcards_enabled`` can be flipped between
calls without a flag fixture or an HTTP round trip.

**This suite could not be executed in the sandbox this ticket was written
in — no Postgres instance is available here.** It is written to run
unmodified against the project's real integration database
(``just test-integration``), following the two sibling files above closely
enough that a human reviewer can check it by inspection.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from aleph import db
from aleph.models import (
    Flashcard,
    FlashcardGrade,
    Lesson,
    LessonGenerationState,
    Level,
    Path,
    PathStatus,
    Unit,
    User,
)
from aleph.repositories.flashcards import FlashcardRepository
from aleph.services.progress_read import load_progress_summary

from .conftest import create_user

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

GENERATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


async def _seed_path_and_lesson(
    session: AsyncSession,
    *,
    user: User,
    topic: str = "Rust ownership",
    completed_at: datetime | None,
) -> tuple[Path, Lesson]:
    """A bare path + one unit + one lesson, completed (or not) at ``completed_at``."""
    path = Path(
        user_id=user.id,
        topic=topic,
        level=Level.SOME_EXPERIENCE,
        status=PathStatus.READY,
    )
    unit = Unit(path=path, position=1, title="Foundations", summary="The basics.")
    lesson = Lesson(
        unit=unit,
        path=path,
        position_in_path=1,
        position_in_unit=1,
        title="What ownership is",
        generation_state=LessonGenerationState.GENERATED,
        read_passage="Ownership is Rust's memory model.",
        generated_at=GENERATED_AT,
        completed_at=completed_at,
    )
    session.add_all([path, unit, lesson])
    await session.flush()
    return path, lesson


async def _kept_card(
    session: AsyncSession, *, user: User, lesson: Lesson, path: Path, due_on: date
) -> Flashcard:
    card = Flashcard(
        user_id=user.id,
        front="What owns a value?",
        back="The variable it is bound to.",
        kept_at=datetime.now(UTC),
        rung=0,
        due_on=due_on,
        source_lesson_id=lesson.id,
        source_path_id=path.id,
        source_lesson_title=lesson.title,
        source_path_title=path.topic,
        source_generated_at=GENERATED_AT,
    )
    session.add(card)
    await session.flush()
    return card


# --------------------------------------------------------------------------- #
# D11's whole claim, both halves in one test — real Postgres
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_review_only_day_is_active_globally_but_not_for_the_path_streak() -> (
    None
):
    """The property ``test_progress_read.py`` proves against fakes, here
    against the real ``review_days_for_user`` query and the real
    ``load_progress_summary`` composition.

    The path's last lesson completion is five days back — no run reaches
    today from lesson completions alone — and the only thing that happens
    *today* is a flashcard review. The global fold must count today as
    Active; the path's own fold must not.
    """
    today = datetime.now(UTC).date()
    fixed_now = datetime.combine(today, datetime.min.time(), tzinfo=UTC) + timedelta(
        hours=12
    )

    async with db.async_session() as session:
        user = await create_user(session, username="streak-union-both-halves")
        path, lesson = await _seed_path_and_lesson(
            session,
            user=user,
            completed_at=fixed_now - timedelta(days=5),
        )
        card = await _kept_card(
            session, user=user, lesson=lesson, path=path, due_on=today
        )
        await session.commit()
        user_id, card_id = user.id, card.id

    async with db.async_session() as session:
        await FlashcardRepository(session).append_review_and_project(
            card_id=card_id,
            user_id=user_id,
            grade=FlashcardGrade.GOT_IT,
            reviewed_at=fixed_now,
            local_day=today,
            rung_before=0,
            rung_after=1,
            due_on_before=today,
            due_on_after=today + timedelta(days=3),
        )
        await session.commit()

    async with db.async_session() as session:
        view = await load_progress_summary(
            session,
            user_id=user_id,
            tz_offset_minutes=0,
            flashcards_enabled=True,
            now=fixed_now,
        )

    # Global: today is Active via the review alone (the lesson completion was
    # five days ago and carries no run into today).
    assert view.current_streak == 1
    # completed_today stays lesson completions only — a review is not a
    # lesson, and the wire field means "N lessons today".
    assert view.completed_today == 0
    # The activity strip cannot contradict the streak: today's cell is
    # non-empty even though no lesson was completed today.
    today_cell = next(cell for cell in view.activity if cell.day == today)
    assert today_cell.count >= 1

    # Per-path: the review never reaches the path's own fold. Its last active
    # day was five days ago, so its current streak is broken (0), untouched
    # by the global signal.
    assert len(view.paths) == 1
    assert view.paths[0].path_id == path.id
    assert view.paths[0].current_streak == 0
    assert view.paths[0].completed_today == 0


# --------------------------------------------------------------------------- #
# D10: the flag gates the second reader
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_flag_off_reverts_the_summary_to_lesson_completions_alone() -> None:
    """Same review as above, but read once with ``flashcards_enabled=False``:
    the summary must come back exactly as it would have before Phase 3
    shipped — bit-identical to Phase 5's own output, not merely "close".
    """
    today = datetime.now(UTC).date()
    fixed_now = datetime.combine(today, datetime.min.time(), tzinfo=UTC) + timedelta(
        hours=12
    )

    async with db.async_session() as session:
        user = await create_user(session, username="streak-union-flag-off")
        path, lesson = await _seed_path_and_lesson(
            session,
            user=user,
            completed_at=fixed_now - timedelta(days=5),
        )
        card = await _kept_card(
            session, user=user, lesson=lesson, path=path, due_on=today
        )
        await session.commit()
        user_id, card_id = user.id, card.id

    async with db.async_session() as session:
        await FlashcardRepository(session).append_review_and_project(
            card_id=card_id,
            user_id=user_id,
            grade=FlashcardGrade.GOT_IT,
            reviewed_at=fixed_now,
            local_day=today,
            rung_before=0,
            rung_after=1,
            due_on_before=today,
            due_on_after=today + timedelta(days=3),
        )
        await session.commit()

    async with db.async_session() as session:
        flag_off = await load_progress_summary(
            session,
            user_id=user_id,
            tz_offset_minutes=0,
            flashcards_enabled=False,
            now=fixed_now,
        )
    async with db.async_session() as session:
        flag_on = await load_progress_summary(
            session,
            user_id=user_id,
            tz_offset_minutes=0,
            flashcards_enabled=True,
            now=fixed_now,
        )

    # Off: today carries no lesson completion and (with the flag off) no
    # review either, so neither today nor yesterday is Active — the grace-day
    # anchor finds nothing and the streak is 0, exactly Phase 5's own answer
    # for this data.
    assert flag_off.current_streak == 0
    assert flag_off.completed_today == 0
    today_cell_off = next(cell for cell in flag_off.activity if cell.day == today)
    assert today_cell_off.count == 0

    # On: the same review now widens the union — the only difference between
    # the two payloads is the flag.
    assert flag_on.current_streak == 1

    # The per-path breakdown is identical either way — the flag only ever
    # touches the global fold.
    assert flag_off.paths == flag_on.paths
