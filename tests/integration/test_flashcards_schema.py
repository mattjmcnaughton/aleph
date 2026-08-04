"""Flashcards schema integration tests against a real per-test Postgres database.

Covers the load-bearing schema properties migration ``0010`` / TDD §4 promise,
and the two SQL invariants the repository owns (§5.3, §5.5):

* **The partial index is the hot path** (§4 item 1) — declared on both the
  model and the migration, and genuinely partial (excludes drafts).
* **Both source FKs are ``SET NULL``; ``user_id``/``card_id`` cascade** (§4
  items 3-4) — a card outlives its source and dies with its account.
* **The candidate query's pin (D3)** — grading a card leaves ``due_candidates``
  reporting the same row with the same start-of-day ``due_on``.
* **Ownership lives on the row itself** — another learner's cards never appear.
* **The review-day expression is UTC-pinned** (D11/§5.5) — unchanged under
  ``SET TIME ZONE`` (the Phase 5 §14 R1 guard, extended).
* **The keep transaction (§5.2)** leaves exactly the kept rows, with the
  discarded drafts gone rather than soft-deleted.

Written in the ``test_schema.py``/``test_shaping_schema.py`` style: real
Postgres (fakes over mocks is for pure logic; cascades and partial indexes are
decided by the database).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy import Table, delete, select, text, update

from aleph import db
from aleph.models import (
    Flashcard,
    FlashcardDraftRun,
    FlashcardDraftRunState,
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

from .conftest import create_user

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

GENERATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


async def _build_path_and_lesson(
    session: AsyncSession, *, user: User, topic: str = "Rust ownership"
) -> tuple[Path, Lesson]:
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
    )
    session.add_all([path, unit, lesson])
    await session.flush()
    return path, lesson


async def _kept_card(
    session: AsyncSession,
    *,
    user: User,
    lesson: Lesson,
    path: Path,
    due_on: date,
    rung: int = 0,
    front: str = "What owns a value?",
    back: str = "The variable it is bound to.",
) -> Flashcard:
    card = Flashcard(
        user_id=user.id,
        front=front,
        back=back,
        kept_at=datetime.now(UTC),
        rung=rung,
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
# The partial index (§4 item 1)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_due_on_index_is_declared_on_both_model_and_migration() -> None:
    """Mirrors ``test_the_streak_index_is_declared_on_both_model_and_migration``
    (``test_schema.py``, Phase 5 D6): the model's ``__table_args__`` and the
    migration that actually creates the index in a real database must agree,
    and the index must genuinely be partial — that partiality *is* "excludes
    drafts" (§4 item 1): a draft's ``kept_at`` is ``NULL`` by definition (D6).

    ``deleted_at IS NULL`` (AL-410, migration ``0011``) is asserted here too —
    the predicate was **rewritten**, not left alone, so the hot path a
    soft-deleted card must never sit in actually excludes it.
    """
    flashcards_table = cast("Table", Flashcard.__table__)
    model_index = next(
        index
        for index in flashcards_table.indexes
        if index.name == "ix_flashcards_user_id_due_on"
    )
    assert [column.name for column in model_index.columns] == ["user_id", "due_on"]
    assert model_index.dialect_options["postgresql"]["where"] is not None

    async with db.async_session() as session:
        indexdef = await session.scalar(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'flashcards' AND indexname = :name"
            ),
            {"name": "ix_flashcards_user_id_due_on"},
        )

    assert indexdef is not None
    assert "user_id" in indexdef
    assert "due_on" in indexdef
    assert "kept_at IS NOT NULL" in indexdef
    assert "deleted_at IS NULL" in indexdef


@pytest.mark.anyio
async def test_the_kept_at_index_is_declared_on_both_model_and_migration() -> None:
    """The card list's own ordering index (AL-410 §2), the same shape as
    :func:`test_the_due_on_index_is_declared_on_both_model_and_migration`
    above: model and migration must agree, the index must be partial, and it
    must genuinely sort ``kept_at`` descending (``ORDER BY kept_at DESC, id
    DESC`` — the list's most-recently-kept-first order)."""
    flashcards_table = cast("Table", Flashcard.__table__)
    model_index = next(
        index
        for index in flashcards_table.indexes
        if index.name == "ix_flashcards_user_id_kept_at"
    )
    assert model_index.dialect_options["postgresql"]["where"] is not None

    async with db.async_session() as session:
        indexdef = await session.scalar(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'flashcards' AND indexname = :name"
            ),
            {"name": "ix_flashcards_user_id_kept_at"},
        )

    assert indexdef is not None
    assert "user_id" in indexdef
    assert "kept_at" in indexdef
    assert "DESC" in indexdef
    assert "kept_at IS NOT NULL" in indexdef
    assert "deleted_at IS NULL" in indexdef


# --------------------------------------------------------------------------- #
# Cascade / SET NULL (§4 items 3-4)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_deleting_the_source_path_nulls_both_fks_and_the_card_survives() -> None:
    """D12: deleting a path cascades to its lessons, which nulls *both* source
    FKs on the card in one delete — and the copied titles keep it renderable.
    """
    async with db.async_session() as session:
        user = await create_user(session)
        path, lesson = await _build_path_and_lesson(session, user=user)
        card = await _kept_card(
            session, user=user, lesson=lesson, path=path, due_on=date(2026, 8, 5)
        )
        await session.commit()
        card_id = card.id
        path_id = path.id

    async with db.async_session() as session:
        await session.execute(delete(Path).where(Path.id == path_id))
        await session.commit()

    async with db.async_session() as session:
        survivor = await session.get(Flashcard, card_id)
        assert survivor is not None
        assert survivor.source_lesson_id is None
        assert survivor.source_path_id is None
        assert survivor.source_lesson_title == "What ownership is"
        assert survivor.source_path_title == "Rust ownership"


@pytest.mark.anyio
async def test_deleting_the_user_cascades_their_cards_and_reviews() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path, lesson = await _build_path_and_lesson(session, user=user)
        card = await _kept_card(
            session, user=user, lesson=lesson, path=path, due_on=date(2026, 8, 5)
        )
        repo = FlashcardRepository(session)
        await repo.append_review_and_project(
            card_id=card.id,
            user_id=user.id,
            grade=FlashcardGrade.GOT_IT,
            reviewed_at=datetime(2026, 8, 4, 12, tzinfo=UTC),
            local_day=date(2026, 8, 4),
            rung_before=0,
            rung_after=1,
            due_on_before=date(2026, 8, 5),
            due_on_after=date(2026, 8, 8),
        )
        await session.commit()
        user_id = user.id
        card_id = card.id

    async with db.async_session() as session:
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()

    async with db.async_session() as session:
        assert await session.get(Flashcard, card_id) is None
        repo = FlashcardRepository(session)
        assert await repo.reviews_for_card(user_id=user_id, card_id=card_id) == []


@pytest.mark.anyio
async def test_deleting_a_card_cascades_only_its_own_reviews() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path, lesson = await _build_path_and_lesson(session, user=user)
        kept = await _kept_card(
            session, user=user, lesson=lesson, path=path, due_on=date(2026, 8, 5)
        )
        other = await _kept_card(
            session,
            user=user,
            lesson=lesson,
            path=path,
            due_on=date(2026, 8, 5),
            front="Second card",
        )
        repo = FlashcardRepository(session)
        await repo.append_review_and_project(
            card_id=kept.id,
            user_id=user.id,
            grade=FlashcardGrade.AGAIN,
            reviewed_at=datetime(2026, 8, 4, 12, tzinfo=UTC),
            local_day=date(2026, 8, 4),
            rung_before=0,
            rung_after=0,
            due_on_before=date(2026, 8, 5),
            due_on_after=date(2026, 8, 4),
        )
        await repo.append_review_and_project(
            card_id=other.id,
            user_id=user.id,
            grade=FlashcardGrade.GOT_IT,
            reviewed_at=datetime(2026, 8, 4, 12, tzinfo=UTC),
            local_day=date(2026, 8, 4),
            rung_before=0,
            rung_after=1,
            due_on_before=date(2026, 8, 5),
            due_on_after=date(2026, 8, 8),
        )
        await session.commit()
        user_id = user.id
        deleted_id, surviving_id = kept.id, other.id

    async with db.async_session() as session:
        await session.execute(delete(Flashcard).where(Flashcard.id == deleted_id))
        await session.commit()

    async with db.async_session() as session:
        repo = FlashcardRepository(session)
        assert await repo.reviews_for_card(user_id=user_id, card_id=deleted_id) == []
        surviving_reviews = await repo.reviews_for_card(
            user_id=user_id, card_id=surviving_id
        )
        assert len(surviving_reviews) == 1


# --------------------------------------------------------------------------- #
# The candidate query's pin (D3/§5.3) — the invariant a test is named for
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_due_candidates_is_invariant_under_grading() -> None:
    """D3's whole correctness claim: grade a due card and re-derive — same
    candidate, same start-of-day ``due_on``, now ``satisfied`` — even though the
    *live* projection on ``flashcards.due_on`` has moved into the future.
    """
    today = date(2026, 8, 4)
    async with db.async_session() as session:
        user = await create_user(session)
        path, lesson = await _build_path_and_lesson(session, user=user)
        card = await _kept_card(
            session, user=user, lesson=lesson, path=path, due_on=today, rung=2
        )
        await session.commit()
        user_id, card_id = user.id, card.id

    async with db.async_session() as session:
        before = await FlashcardRepository(session).due_candidates(
            user_id=user_id, today=today
        )
    assert [(c.card_id, c.due_on, c.satisfied) for c in before] == [
        (card_id, today, False)
    ]

    async with db.async_session() as session:
        repo = FlashcardRepository(session)
        await repo.append_review_and_project(
            card_id=card_id,
            user_id=user_id,
            grade=FlashcardGrade.GOT_IT,
            reviewed_at=datetime(2026, 8, 4, 12, tzinfo=UTC),
            local_day=today,
            rung_before=2,
            rung_after=3,
            due_on_before=today,
            due_on_after=today + timedelta(days=14),
        )
        await session.commit()

    async with db.async_session() as session:
        after = await FlashcardRepository(session).due_candidates(
            user_id=user_id, today=today
        )
    assert len(after) == 1
    assert after[0].card_id == card_id
    # Pinned to the start-of-day value, not the live (now future) projection.
    assert after[0].due_on == today
    assert after[0].satisfied is True

    async with db.async_session() as session:
        live_due_on = await session.scalar(
            select(Flashcard.due_on).where(Flashcard.id == card_id)
        )
    assert live_due_on == today + timedelta(days=14)


@pytest.mark.anyio
async def test_due_candidates_still_pinned_after_a_same_day_lapse() -> None:
    """The pin holds through a lapse too: ``AGAIN`` sets ``due_on_after = today``,
    so the live projection does not even leave the ``due_on <= today`` arm — and
    the candidate is still exactly one row with the unchanged start-of-day
    ``due_on``.
    """
    today = date(2026, 8, 4)
    async with db.async_session() as session:
        user = await create_user(session)
        path, lesson = await _build_path_and_lesson(session, user=user)
        card = await _kept_card(
            session, user=user, lesson=lesson, path=path, due_on=today, rung=2
        )
        await session.commit()
        user_id, card_id = user.id, card.id

    async with db.async_session() as session:
        repo = FlashcardRepository(session)
        await repo.append_review_and_project(
            card_id=card_id,
            user_id=user_id,
            grade=FlashcardGrade.AGAIN,
            reviewed_at=datetime(2026, 8, 4, 9, tzinfo=UTC),
            local_day=today,
            rung_before=2,
            rung_after=1,
            due_on_before=today,
            due_on_after=today,
        )
        await session.commit()

    async with db.async_session() as session:
        after = await FlashcardRepository(session).due_candidates(
            user_id=user_id, today=today
        )
    assert [(c.card_id, c.due_on, c.satisfied) for c in after] == [
        (card_id, today, False)
    ]


# --------------------------------------------------------------------------- #
# Ownership (§4 item 3)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_another_learners_cards_never_appear_in_due_candidates() -> None:
    today = date(2026, 8, 4)
    async with db.async_session() as session:
        owner = await create_user(session, username="owner", subject="owner-sub")
        other = await create_user(session, username="other", subject="other-sub")
        owner_path, owner_lesson = await _build_path_and_lesson(session, user=owner)
        other_path, other_lesson = await _build_path_and_lesson(
            session, user=other, topic="US healthcare"
        )
        owner_card = await _kept_card(
            session, user=owner, lesson=owner_lesson, path=owner_path, due_on=today
        )
        await _kept_card(
            session, user=other, lesson=other_lesson, path=other_path, due_on=today
        )
        await session.commit()
        owner_id, owner_card_id = owner.id, owner_card.id

    async with db.async_session() as session:
        candidates = await FlashcardRepository(session).due_candidates(
            user_id=owner_id, today=today
        )
    assert [c.card_id for c in candidates] == [owner_card_id]


@pytest.mark.anyio
async def test_another_learners_cards_never_appear_in_cards_by_ids() -> None:
    today = date(2026, 8, 4)
    async with db.async_session() as session:
        owner = await create_user(session, username="owner2", subject="owner2-sub")
        other = await create_user(session, username="other2", subject="other2-sub")
        owner_path, owner_lesson = await _build_path_and_lesson(session, user=owner)
        other_path, other_lesson = await _build_path_and_lesson(
            session, user=other, topic="US healthcare"
        )
        await _kept_card(
            session, user=owner, lesson=owner_lesson, path=owner_path, due_on=today
        )
        other_card = await _kept_card(
            session, user=other, lesson=other_lesson, path=other_path, due_on=today
        )
        await session.commit()
        owner_id, other_card_id = owner.id, other_card.id

    async with db.async_session() as session:
        records = await FlashcardRepository(session).cards_by_ids(
            user_id=owner_id, card_ids=[other_card_id]
        )
    assert records == []


# --------------------------------------------------------------------------- #
# The streak union's day expression (D11/§5.5) — the Phase 5 §14 R1 guard
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_review_days_for_user_is_unchanged_under_a_session_time_zone() -> None:
    """Casting a bare ``timestamptz`` to ``date`` resolves in the session's
    ``TimeZone`` GUC; the shipped expression pins to UTC first so it does not.
    Same guard as ``test_progress_api.py``'s, extended to the new query.
    """
    async with db.async_session() as session:
        user = await create_user(session)
        path, lesson = await _build_path_and_lesson(session, user=user)
        card = await _kept_card(
            session, user=user, lesson=lesson, path=path, due_on=date(2026, 1, 1)
        )
        repo = FlashcardRepository(session)
        await repo.append_review_and_project(
            card_id=card.id,
            user_id=user.id,
            grade=FlashcardGrade.GOT_IT,
            # Late in the UTC day: a naive local cast under a US timezone would
            # roll this back to 2025-12-31.
            reviewed_at=datetime(2026, 1, 1, 23, 30, tzinfo=UTC),
            local_day=date(2026, 1, 1),
            rung_before=0,
            rung_after=1,
            due_on_before=date(2026, 1, 1),
            due_on_after=date(2026, 1, 4),
        )
        await session.commit()
        user_id = user.id

    async with db.async_session() as session:
        under_utc = await FlashcardRepository(session).review_days_for_user(
            user_id=user_id, tz_offset_minutes=0
        )

    async with db.async_session() as session:
        await session.execute(text("SET TIME ZONE 'America/Chicago'"))
        under_chicago = await FlashcardRepository(session).review_days_for_user(
            user_id=user_id, tz_offset_minutes=0
        )

    assert [d.isoformat() for d in under_utc] == ["2026-01-01"]
    assert [d.isoformat() for d in under_chicago] == ["2026-01-01"]


# --------------------------------------------------------------------------- #
# The keep transaction (§5.2)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_keep_drafts_leaves_exactly_the_kept_rows() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path, lesson = await _build_path_and_lesson(session, user=user)
        repo = FlashcardRepository(session)
        drafts = await repo.create_drafts(
            user_id=user.id,
            source_lesson_id=lesson.id,
            source_path_id=path.id,
            source_lesson_title=lesson.title,
            source_path_title=path.topic,
            source_generated_at=GENERATED_AT,
            cards=[("Q1", "A1"), ("Q2", "A2"), ("Q3", "A3"), ("Q4", "A4")],
        )
        await session.commit()
        lesson_id = lesson.id
        keep_ids = [drafts[0].id, drafts[2].id]
        discard_ids = {drafts[1].id, drafts[3].id}

    async with db.async_session() as session:
        kept_count = await FlashcardRepository(session).keep_drafts(
            lesson_id=lesson_id, kept_ids=keep_ids, due_on=date(2026, 8, 5)
        )
        await session.commit()
    assert kept_count == 2

    async with db.async_session() as session:
        remaining = (
            (
                await session.execute(
                    select(Flashcard).where(Flashcard.source_lesson_id == lesson_id)
                )
            )
            .scalars()
            .all()
        )
    remaining_ids = {card.id for card in remaining}
    assert remaining_ids == set(keep_ids)
    assert remaining_ids.isdisjoint(discard_ids)
    assert all(card.kept_at is not None for card in remaining)
    assert all(card.rung == 0 for card in remaining)
    assert all(card.due_on == date(2026, 8, 5) for card in remaining)


@pytest.mark.anyio
async def test_keep_drafts_with_no_ids_discards_every_draft() -> None:
    """ "Skip — keep none" (D6): the same request shape, an empty list."""
    async with db.async_session() as session:
        user = await create_user(session)
        path, lesson = await _build_path_and_lesson(session, user=user)
        repo = FlashcardRepository(session)
        await repo.create_drafts(
            user_id=user.id,
            source_lesson_id=lesson.id,
            source_path_id=path.id,
            source_lesson_title=lesson.title,
            source_path_title=path.topic,
            source_generated_at=GENERATED_AT,
            cards=[("Q1", "A1"), ("Q2", "A2")],
        )
        await session.commit()
        lesson_id = lesson.id

    async with db.async_session() as session:
        kept_count = await FlashcardRepository(session).keep_drafts(
            lesson_id=lesson_id, kept_ids=[], due_on=date(2026, 8, 5)
        )
        await session.commit()
    assert kept_count == 0

    async with db.async_session() as session:
        remaining = await session.scalar(
            select(Flashcard).where(Flashcard.source_lesson_id == lesson_id).limit(1)
        )
    assert remaining is None


@pytest.mark.anyio
async def test_keeping_an_id_from_another_lesson_reports_it_unkept() -> None:
    """The scoping the ``404``-never-silent-skip contract rests on: an id that
    belongs to a *different* lesson's drafts is not touched, and the returned
    count is short by exactly that many — the caller's signal to 404.
    """
    async with db.async_session() as session:
        user = await create_user(session)
        path_a, lesson_a = await _build_path_and_lesson(session, user=user, topic="A")
        path_b, lesson_b = await _build_path_and_lesson(session, user=user, topic="B")
        repo = FlashcardRepository(session)
        drafts_a = await repo.create_drafts(
            user_id=user.id,
            source_lesson_id=lesson_a.id,
            source_path_id=path_a.id,
            source_lesson_title=lesson_a.title,
            source_path_title=path_a.topic,
            source_generated_at=GENERATED_AT,
            cards=[("Q1", "A1")],
        )
        drafts_b = await repo.create_drafts(
            user_id=user.id,
            source_lesson_id=lesson_b.id,
            source_path_id=path_b.id,
            source_lesson_title=lesson_b.title,
            source_path_title=path_b.topic,
            source_generated_at=GENERATED_AT,
            cards=[("Q2", "A2")],
        )
        await session.commit()
        lesson_a_id = lesson_a.id
        foreign_id = drafts_b[0].id
        own_id = drafts_a[0].id

    async with db.async_session() as session:
        kept_count = await FlashcardRepository(session).keep_drafts(
            lesson_id=lesson_a_id,
            kept_ids=[own_id, foreign_id],
            due_on=date(2026, 8, 5),
        )
        await session.commit()
    # Only the own-lesson id was actually updated — the caller sees 1 < 2 and
    # knows ``foreign_id`` was bogus for this lesson.
    assert kept_count == 1

    async with db.async_session() as session:
        # The other lesson's draft is untouched (still a draft, not kept).
        other_draft = await session.get(Flashcard, foreign_id)
        assert other_draft is not None
        assert other_draft.kept_at is None


# --------------------------------------------------------------------------- #
# The claim protocol (D7/§5.2) — claim_draft_run's re-claim guard
#
# The regression this section exists to catch (review finding #1, BLOCKER):
# the staleness arm has to be conjoined with `state == GENERATING`, not merely
# `OR`ed into the update's WHERE clause, or an already-`generated` run older
# than the stale window gets re-claimed and drafted a second time — silently
# breaking D5/D7's "drafting a lesson twice is structurally impossible".
# --------------------------------------------------------------------------- #


async def _backdate_run_started_at(
    session: AsyncSession, *, lesson_id: uuid.UUID, started_at: datetime
) -> None:
    """Rewrite a run's ``started_at`` directly, bypassing the claim protocol —
    the only way to construct a "stale" fixture without sleeping in a test."""
    await session.execute(
        update(FlashcardDraftRun)
        .where(FlashcardDraftRun.lesson_id == lesson_id)
        .values(started_at=started_at)
    )
    await session.commit()


@pytest.mark.anyio
async def test_first_claim_wins() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        _, lesson = await _build_path_and_lesson(session, user=user)
        await session.commit()
        lesson_id = lesson.id

    async with db.async_session() as session:
        repo = FlashcardRepository(session)
        fence = await repo.claim_draft_run(lesson_id=lesson_id, stale_after_seconds=180)
        await session.commit()
    assert fence is not None

    async with db.async_session() as session:
        run = await FlashcardRepository(session).get_draft_run(lesson_id)
    assert run is not None
    assert run.state == FlashcardDraftRunState.GENERATING
    assert run.started_at == fence


@pytest.mark.anyio
async def test_a_second_claim_while_generating_loses() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        _, lesson = await _build_path_and_lesson(session, user=user)
        await session.commit()
        lesson_id = lesson.id

    async with db.async_session() as session:
        repo = FlashcardRepository(session)
        first = await repo.claim_draft_run(lesson_id=lesson_id, stale_after_seconds=180)
        await session.commit()
    assert first is not None

    async with db.async_session() as session:
        repo = FlashcardRepository(session)
        second = await repo.claim_draft_run(
            lesson_id=lesson_id, stale_after_seconds=180
        )
        await session.commit()
    assert second is None

    async with db.async_session() as session:
        run = await FlashcardRepository(session).get_draft_run(lesson_id)
    assert run is not None
    assert run.state == FlashcardDraftRunState.GENERATING
    assert run.started_at == first


@pytest.mark.anyio
async def test_a_stale_generated_run_still_loses_its_claim() -> None:
    """The bug this whole section exists to pin: ``generated`` is terminal.

    A run resolved to ``generated`` **long** before the stale cutoff must never
    match the re-claim arm, no matter how old ``started_at`` is — a buggy
    ``OR started_at < stale_cutoff`` (unconjoined with ``state == GENERATING``)
    would re-claim it here and let the agent draft the lesson a second time.
    """
    async with db.async_session() as session:
        user = await create_user(session)
        _, lesson = await _build_path_and_lesson(session, user=user)
        await session.commit()
        lesson_id = lesson.id

    async with db.async_session() as session:
        repo = FlashcardRepository(session)
        fence = await repo.claim_draft_run(lesson_id=lesson_id, stale_after_seconds=180)
        assert fence is not None
        generated = await repo.mark_draft_run_generated(
            lesson_id=lesson_id, fence=fence
        )
        assert generated is True
        await session.commit()

    # Long past any reasonable stale window — if the predicate were the
    # unconjoined `OR`, this alone would make the run re-claimable.
    ancient = datetime.now(UTC) - timedelta(days=1)
    async with db.async_session() as session:
        await _backdate_run_started_at(session, lesson_id=lesson_id, started_at=ancient)

    async with db.async_session() as session:
        repo = FlashcardRepository(session)
        reclaim = await repo.claim_draft_run(
            lesson_id=lesson_id, stale_after_seconds=180
        )
        await session.commit()
    assert reclaim is None

    async with db.async_session() as session:
        run = await FlashcardRepository(session).get_draft_run(lesson_id)
    assert run is not None
    assert run.state == FlashcardDraftRunState.GENERATED
    assert run.started_at == ancient


@pytest.mark.anyio
async def test_a_failed_run_is_reclaimable() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        _, lesson = await _build_path_and_lesson(session, user=user)
        await session.commit()
        lesson_id = lesson.id

    async with db.async_session() as session:
        repo = FlashcardRepository(session)
        fence = await repo.claim_draft_run(lesson_id=lesson_id, stale_after_seconds=180)
        assert fence is not None
        failed = await repo.mark_draft_run_failed(
            lesson_id=lesson_id, error="boom", fence=fence
        )
        assert failed is True
        await session.commit()

    async with db.async_session() as session:
        repo = FlashcardRepository(session)
        reclaim = await repo.claim_draft_run(
            lesson_id=lesson_id, stale_after_seconds=180
        )
        await session.commit()
    assert reclaim is not None
    assert reclaim != fence

    async with db.async_session() as session:
        run = await FlashcardRepository(session).get_draft_run(lesson_id)
    assert run is not None
    assert run.state == FlashcardDraftRunState.GENERATING
    assert run.error is None


@pytest.mark.anyio
async def test_a_stale_generating_run_is_reclaimable() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        _, lesson = await _build_path_and_lesson(session, user=user)
        await session.commit()
        lesson_id = lesson.id

    async with db.async_session() as session:
        repo = FlashcardRepository(session)
        fence = await repo.claim_draft_run(lesson_id=lesson_id, stale_after_seconds=180)
        assert fence is not None
        await session.commit()

    stale = datetime.now(UTC) - timedelta(seconds=300)
    async with db.async_session() as session:
        await _backdate_run_started_at(session, lesson_id=lesson_id, started_at=stale)

    async with db.async_session() as session:
        repo = FlashcardRepository(session)
        reclaim = await repo.claim_draft_run(
            lesson_id=lesson_id, stale_after_seconds=180
        )
        await session.commit()
    assert reclaim is not None
    assert reclaim != stale

    async with db.async_session() as session:
        run = await FlashcardRepository(session).get_draft_run(lesson_id)
    assert run is not None
    assert run.state == FlashcardDraftRunState.GENERATING
    assert run.started_at == reclaim


# --------------------------------------------------------------------------- #
# AL-410 review finding 4: ``list_cards_for_user``'s ``limit`` clamp against
# the real repository (not the unit-test fake — that pins the fake's own
# mirror of this clamp, this pins the SQL). The router's own ``Query(20, ge=1,
# le=...)`` never lets ``limit <= 0`` reach this method, but the docstring
# sells the repo-level cap as protection "for a caller that reaches this
# repository directly, bypassing the router" — this test *is* that caller.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_list_cards_for_user_floors_a_non_positive_limit_instead_of_500ing() -> (
    None
):
    """Before the fix, ``capped_limit = min(limit, MAX_CARD_LIST_LIMIT)`` left
    ``limit=0`` uncapped from below: the lookahead fetch (``capped_limit + 1
    == 1``) still returns a row, ``has_more`` is ``True``, ``page_rows`` (the
    first ``0`` of those rows) is empty, and ``page_rows[-1]`` in the
    ``next_cursor`` branch raises ``IndexError`` — a bare-caller ``500``, not
    the clamp the docstring promises. ``max(1, min(...))`` floors it instead:
    a non-positive ``limit`` degrades to "give me one row", never a crash.
    """
    async with db.async_session() as session:
        user = await create_user(session)
        path, lesson = await _build_path_and_lesson(session, user=user)
        await _kept_card(
            session, user=user, lesson=lesson, path=path, due_on=date(2026, 8, 5)
        )
        await _kept_card(
            session, user=user, lesson=lesson, path=path, due_on=date(2026, 8, 5)
        )
        await session.commit()
        user_id = user.id

    async with db.async_session() as session:
        zero_page = await FlashcardRepository(session).list_cards_for_user(
            user_id=user_id, limit=0, cursor=None, path_id=None, query=None
        )
    assert len(zero_page.cards) == 1  # floored to 1, not 0
    assert zero_page.next_cursor is not None  # a second card is still waiting

    async with db.async_session() as session:
        negative_page = await FlashcardRepository(session).list_cards_for_user(
            user_id=user_id, limit=-5, cursor=None, path_id=None, query=None
        )
    assert len(negative_page.cards) == 1
    assert negative_page.next_cursor is not None
