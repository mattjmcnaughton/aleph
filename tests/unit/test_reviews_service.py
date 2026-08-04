"""Unit tests for `aleph.services.reviews` (Phase 3 TDD §11).

Against a fake repository behind `QueueReader`/`GradeStore` (CLAUDE.md: fakes
over mocks) — no session, no Postgres. `InMemoryFlashcards.due_candidates`
mirrors `FlashcardRepository.due_candidates`'s SQL invariant (§5.3: a card
graded today is kept in the population at its *start-of-day* `due_on`, via an
in-memory review log rather than a `COALESCE` over a CTE) — which is what
makes "grade three through `_grade`, re-derive through `_load_queue`, same
ten" an actual test of the derivation rather than an assumption about it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from fastapi import HTTPException

from aleph.models import Flashcard, FlashcardGrade
from aleph.repositories.flashcards import DueCandidate, FlashcardRecord
from aleph.services.reviews import PathDueView, _grade, _load_queue, _load_summary

if TYPE_CHECKING:
    import uuid as _uuid
    from collections.abc import Sequence

    from aleph.models import FlashcardReview
    from aleph.services.reviews import ReviewQueueView

_USER = uuid.uuid4()
_TODAY = date(2026, 8, 4)
_NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --------------------------------------------------------------------------- #
# The fake (CLAUDE.md: fakes over mocks)
# --------------------------------------------------------------------------- #


@dataclass
class _Review:
    card_id: uuid.UUID
    grade: FlashcardGrade
    reviewed_at: datetime
    local_day: date
    due_on_before: date


@dataclass
class InMemoryFlashcards:
    """A `QueueReader` + `GradeStore` fake with a growing review log, so the
    D3 pin holds for the fake exactly as it holds for the real SQL."""

    cards: dict[uuid.UUID, Flashcard] = field(default_factory=dict)
    # source_lesson_id -> its *live* generated_at, for D12's citation
    # judgement. A lesson absent here (deleted, or never registered) reads as
    # "gone" — `current_lesson_generated_at` resolves to `None`.
    lesson_generated_at: dict[uuid.UUID, datetime] = field(default_factory=dict)
    reviews: list[_Review] = field(default_factory=list)

    def add_card(
        self, card: Flashcard, *, lesson_generated_at: datetime | None = None
    ) -> None:
        self.cards[card.id] = card
        if card.source_lesson_id is not None and lesson_generated_at is not None:
            self.lesson_generated_at[card.source_lesson_id] = lesson_generated_at

    async def due_candidates(
        self, *, user_id: _uuid.UUID, today: date
    ) -> list[DueCandidate]:
        first_today: dict[uuid.UUID, date] = {}
        latest_today: dict[uuid.UUID, tuple[FlashcardGrade, datetime]] = {}
        for review in sorted(self.reviews, key=lambda r: r.reviewed_at):
            if review.local_day != today:
                continue
            card = self.cards.get(review.card_id)
            if card is None or card.user_id != user_id:
                continue
            first_today.setdefault(review.card_id, review.due_on_before)
            latest_today[review.card_id] = (review.grade, review.reviewed_at)

        results: list[DueCandidate] = []
        for card in self.cards.values():
            if card.user_id != user_id or card.kept_at is None:
                continue
            reviewed_today = card.id in first_today
            live_due_on = card.due_on
            assert live_due_on is not None
            if not (live_due_on <= today or reviewed_today):
                continue
            due_on = first_today.get(card.id, live_due_on)
            satisfied = (
                card.id in latest_today
                and latest_today[card.id][0] == FlashcardGrade.GOT_IT
            )
            last_reviewed_at = (
                latest_today[card.id][1] if card.id in latest_today else None
            )
            results.append(
                DueCandidate(
                    card_id=card.id,
                    due_on=due_on,
                    satisfied=satisfied,
                    last_reviewed_at=last_reviewed_at,
                )
            )
        return results

    async def cards_by_ids(
        self, *, user_id: _uuid.UUID, card_ids: Sequence[uuid.UUID]
    ) -> list[FlashcardRecord]:
        records: list[FlashcardRecord] = []
        for card_id in card_ids:
            card = self.cards.get(card_id)
            if card is None or card.user_id != user_id or card.kept_at is None:
                continue
            assert card.rung is not None
            assert card.due_on is not None
            current = (
                self.lesson_generated_at.get(card.source_lesson_id)
                if card.source_lesson_id is not None
                else None
            )
            records.append(
                FlashcardRecord(
                    id=card.id,
                    front=card.front,
                    back=card.back,
                    rung=card.rung,
                    due_on=card.due_on,
                    source_lesson_id=card.source_lesson_id,
                    source_path_id=card.source_path_id,
                    source_lesson_title=card.source_lesson_title,
                    source_path_title=card.source_path_title,
                    source_generated_at=card.source_generated_at,
                    current_lesson_generated_at=current,
                )
            )
        return records

    async def get_card_for_update(
        self, *, user_id: _uuid.UUID, card_id: _uuid.UUID
    ) -> Flashcard | None:
        card = self.cards.get(card_id)
        if card is None or card.user_id != user_id:
            return None
        return card

    async def append_review_and_project(
        self,
        *,
        card_id: _uuid.UUID,
        user_id: _uuid.UUID,
        grade: FlashcardGrade,
        reviewed_at: datetime,
        local_day: date,
        rung_before: int,
        rung_after: int,
        due_on_before: date,
        due_on_after: date,
    ) -> FlashcardReview:
        self.reviews.append(
            _Review(
                card_id=card_id,
                grade=grade,
                reviewed_at=reviewed_at,
                local_day=local_day,
                due_on_before=due_on_before,
            )
        )
        card = self.cards[card_id]
        card.rung = rung_after
        card.due_on = due_on_after
        # The row's own identity is never inspected by anything under test
        # here (only `self.reviews` above is) — a cast stand-in is honest
        # about that, rather than constructing a real (unpersisted) ORM row.
        return cast("FlashcardReview", object())


def _conflict_reason(exc: HTTPException) -> object:
    """`_conflict`'s `details.reason`, narrowed from `HTTPException.detail`'s
    declared `str` type (Starlette) the same way `test_rate_limit.py` narrows
    the plain-string case."""
    detail = exc.detail
    assert isinstance(detail, dict)
    return detail["reason"]


def _rung(card: Flashcard) -> int:
    """`Flashcard.rung` is `int | None` on the model (`None` only for a
    draft); every card this file builds is kept, so it is always an `int` —
    narrowed once here rather than at each of this file's many call sites."""
    assert card.rung is not None
    return card.rung


def _card(
    *,
    due_on: date,
    rung: int = 2,
    source_path_id: uuid.UUID | None,
    source_lesson_id: uuid.UUID | None = None,
    source_generated_at: datetime = _NOW,
    front: str = "front",
) -> Flashcard:
    return Flashcard(
        id=uuid.uuid4(),
        user_id=_USER,
        front=front,
        back="back",
        kept_at=datetime.now(UTC),
        rung=rung,
        due_on=due_on,
        source_lesson_id=source_lesson_id,
        source_path_id=source_path_id,
        source_lesson_title="A lesson",
        source_path_title="A path",
        source_generated_at=source_generated_at,
    )


async def _queue(
    store: InMemoryFlashcards, *, path_id: uuid.UUID | None = None, now: datetime = _NOW
) -> ReviewQueueView:
    return await _load_queue(
        store, user_id=_USER, tz_offset_minutes=0, path_id=path_id, now=now
    )


# --------------------------------------------------------------------------- #
# Serve order: never-attempted first, then lapses least-recently-seen first.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_serve_order_is_never_attempted_first_then_lapses_least_recent() -> None:
    store = InMemoryFlashcards()
    never = _card(due_on=_TODAY, source_path_id=None, front="never-attempted")
    store.add_card(never)
    # A lapse graded earlier today: attempted, unsatisfied, due today (D8).
    lapsed_early = _card(due_on=_TODAY, source_path_id=None, front="lapsed-early")
    store.add_card(lapsed_early)
    lapsed_late = _card(due_on=_TODAY, source_path_id=None, front="lapsed-late")
    store.add_card(lapsed_late)

    # Grade the two lapses first (through `_grade`, so the log is real),
    # earliest first, so their `last_reviewed_at` values are ordered.
    await _grade(
        store,
        user_id=_USER,
        card_id=lapsed_early.id,
        grade=FlashcardGrade.AGAIN,
        rung_before=_rung(lapsed_early),
        tz_offset_minutes=0,
        now=_NOW,
    )
    await _grade(
        store,
        user_id=_USER,
        card_id=lapsed_late.id,
        grade=FlashcardGrade.AGAIN,
        rung_before=_rung(lapsed_late),
        tz_offset_minutes=0,
        now=_NOW + timedelta(minutes=5),
    )

    view = await _queue(store)

    assert [card.front for card in view.cards] == [
        "never-attempted",
        "lapsed-early",
        "lapsed-late",
    ]


# --------------------------------------------------------------------------- #
# completed/total under lapses (a lapse never changes `total`).
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_lapse_never_changes_total_and_completed_counts_only_got_it() -> None:
    store = InMemoryFlashcards()
    a = _card(due_on=_TODAY, source_path_id=None)
    b = _card(due_on=_TODAY, source_path_id=None)
    c = _card(due_on=_TODAY, source_path_id=None)
    for card in (a, b, c):
        store.add_card(card)

    before = await _queue(store)
    assert before.total == 3
    assert before.completed == 0

    await _grade(
        store,
        user_id=_USER,
        card_id=a.id,
        grade=FlashcardGrade.GOT_IT,
        rung_before=_rung(a),
        tz_offset_minutes=0,
        now=_NOW,
    )
    await _grade(
        store,
        user_id=_USER,
        card_id=b.id,
        grade=FlashcardGrade.AGAIN,
        rung_before=_rung(b),
        tz_offset_minutes=0,
        now=_NOW,
    )

    after = await _queue(store)
    assert after.total == 3  # unchanged by either grade
    assert after.completed == 1  # only the `got_it`
    assert {card.front for card in after.cards} == {b.front, c.front}


# --------------------------------------------------------------------------- #
# `path_id` filtering is display-only: `total` stays the global set's size.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_path_filter_leaves_total_alone_and_scopes_cards_only() -> None:
    store = InMemoryFlashcards()
    path_a = uuid.uuid4()
    path_b = uuid.uuid4()
    a1 = _card(due_on=_TODAY, source_path_id=path_a, front="a1")
    a2 = _card(due_on=_TODAY, source_path_id=path_a, front="a2")
    b1 = _card(due_on=_TODAY, source_path_id=path_b, front="b1")
    for card in (a1, a2, b1):
        store.add_card(card)

    unfiltered = await _queue(store)
    filtered = await _queue(store, path_id=path_a)

    assert unfiltered.total == 3
    assert filtered.total == 3  # the denominator never shrinks (§5.3)
    assert {card.front for card in filtered.cards} == {"a1", "a2"}
    assert filtered.scope_path_id == path_a
    assert filtered.other_due_count == 1  # b1, excluded by the filter


@pytest.mark.anyio
async def test_other_due_count_is_zero_in_an_unfiltered_session() -> None:
    store = InMemoryFlashcards()
    path_a = uuid.uuid4()
    store.add_card(_card(due_on=_TODAY, source_path_id=path_a))
    store.add_card(_card(due_on=_TODAY, source_path_id=None))

    view = await _queue(store)

    assert view.scope_path_id is None
    assert view.other_due_count == 0


# --------------------------------------------------------------------------- #
# An orphaned card (D12: no `source_path_id`) appears globally, never filtered.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_an_orphaned_card_appears_globally_and_in_no_filter() -> None:
    store = InMemoryFlashcards()
    path_a = uuid.uuid4()
    orphan = _card(due_on=_TODAY, source_path_id=None, front="orphan")
    store.add_card(orphan)
    store.add_card(_card(due_on=_TODAY, source_path_id=path_a, front="a"))

    unfiltered = await _queue(store)
    filtered = await _queue(store, path_id=path_a)

    assert "orphan" in {card.front for card in unfiltered.cards}
    assert "orphan" not in {card.front for card in filtered.cards}
    assert unfiltered.total == filtered.total == 2


# --------------------------------------------------------------------------- #
# The citation (D12): linked iff the lesson survives and has not moved on.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_citation_is_linked_when_the_lesson_is_unchanged() -> None:
    store = InMemoryFlashcards()
    lesson_id = uuid.uuid4()
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    card = _card(
        due_on=_TODAY,
        source_path_id=uuid.uuid4(),
        source_lesson_id=lesson_id,
        source_generated_at=stamp,
    )
    store.add_card(card, lesson_generated_at=stamp)

    view = await _queue(store)

    assert view.cards[0].source.kind == "linked"
    assert view.cards[0].source.lesson_id == lesson_id


@pytest.mark.anyio
async def test_citation_degrades_when_the_lesson_is_gone() -> None:
    store = InMemoryFlashcards()
    card = _card(due_on=_TODAY, source_path_id=uuid.uuid4(), source_lesson_id=None)
    store.add_card(card)

    view = await _queue(store)

    assert view.cards[0].source.kind == "degraded"
    assert view.cards[0].source.lesson_id is None


@pytest.mark.anyio
async def test_citation_degrades_when_the_lesson_has_been_regenerated() -> None:
    store = InMemoryFlashcards()
    lesson_id = uuid.uuid4()
    drafted_at = datetime(2026, 1, 1, tzinfo=UTC)
    regenerated_at = datetime(2026, 2, 1, tzinfo=UTC)
    card = _card(
        due_on=_TODAY,
        source_path_id=uuid.uuid4(),
        source_lesson_id=lesson_id,
        source_generated_at=drafted_at,
    )
    store.add_card(card, lesson_generated_at=regenerated_at)

    view = await _queue(store)

    assert view.cards[0].source.kind == "degraded"
    assert view.cards[0].source.lesson_id is None


# --------------------------------------------------------------------------- #
# The summary: per-path counts sum to the global `due_count`.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_summary_per_path_counts_sum_to_the_global_due_count() -> None:
    store = InMemoryFlashcards()
    path_a = uuid.uuid4()
    path_b = uuid.uuid4()
    store.add_card(_card(due_on=_TODAY, source_path_id=path_a))
    store.add_card(_card(due_on=_TODAY, source_path_id=path_a))
    store.add_card(_card(due_on=_TODAY, source_path_id=path_b))
    store.add_card(_card(due_on=_TODAY, source_path_id=None))  # orphaned

    summary = await _load_summary(store, user_id=_USER, tz_offset_minutes=0, now=_NOW)

    assert summary.due_count == 4
    by_path = {row.path_id: row.due_count for row in summary.paths}
    assert by_path == {path_a: 2, path_b: 1}
    assert sum(by_path.values()) < summary.due_count  # the orphan is uncounted per-path


# --------------------------------------------------------------------------- #
# Finding 1: `due_count` (and the per-path chips) count the **unsatisfied
# remainder** only, not the whole day's selected set. The test above
# (`test_summary_per_path_counts_sum_to_the_global_due_count`) never grades a
# card, so it passes under either reading — these grade, then re-read.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_summary_due_count_drops_as_cards_are_graded_got_it() -> None:
    store = InMemoryFlashcards()
    path_a = uuid.uuid4()
    cards = [
        _card(due_on=_TODAY, source_path_id=path_a, front=f"c{i}") for i in range(4)
    ]
    for card in cards:
        store.add_card(card)

    before = await _load_summary(store, user_id=_USER, tz_offset_minutes=0, now=_NOW)
    assert before.due_count == 4
    assert before.paths == [PathDueView(path_id=path_a, due_count=4)]

    # Grade three `got_it` — the fourth is left unsatisfied.
    for card in cards[:3]:
        await _grade(
            store,
            user_id=_USER,
            card_id=card.id,
            grade=FlashcardGrade.GOT_IT,
            rung_before=_rung(card),
            tz_offset_minutes=0,
            now=_NOW,
        )

    after = await _load_summary(store, user_id=_USER, tz_offset_minutes=0, now=_NOW)
    # The bug this pins: `_load_summary` used to return `len(selected)` — the
    # whole day's set, unchanged by grading — so `due_count` stayed 4 all day
    # and could never reach zero through work (§8's "hidden entirely at
    # zero"). It must drop to exactly the one un-graded card.
    assert after.due_count == 1
    assert after.paths == [PathDueView(path_id=path_a, due_count=1)]


@pytest.mark.anyio
async def test_summary_due_count_reaches_zero_once_every_card_is_satisfied() -> None:
    store = InMemoryFlashcards()
    card = _card(due_on=_TODAY, source_path_id=None)
    store.add_card(card)

    await _grade(
        store,
        user_id=_USER,
        card_id=card.id,
        grade=FlashcardGrade.GOT_IT,
        rung_before=_rung(card),
        tz_offset_minutes=0,
        now=_NOW,
    )

    summary = await _load_summary(store, user_id=_USER, tz_offset_minutes=0, now=_NOW)

    assert summary.due_count == 0
    assert summary.estimated_minutes == 0
    assert summary.paths == []


@pytest.mark.anyio
async def test_summary_due_count_ignores_an_again_grade() -> None:
    # `again` never satisfies a card (D8) — grading it must not move
    # `due_count`, only its position in the queue's serve order.
    store = InMemoryFlashcards()
    card = _card(due_on=_TODAY, source_path_id=None, rung=2)
    store.add_card(card)

    await _grade(
        store,
        user_id=_USER,
        card_id=card.id,
        grade=FlashcardGrade.AGAIN,
        rung_before=_rung(card),
        tz_offset_minutes=0,
        now=_NOW,
    )

    summary = await _load_summary(store, user_id=_USER, tz_offset_minutes=0, now=_NOW)

    assert summary.due_count == 1


# --------------------------------------------------------------------------- #
# The invariant the frontend needs (ticket 1): `queue.total - queue.completed
# == summary.due_count`, holding before and after grading of every kind.
# --------------------------------------------------------------------------- #


async def _assert_total_minus_completed_equals_due_count(
    store: InMemoryFlashcards,
) -> None:
    queue = await _queue(store)
    summary = await _load_summary(store, user_id=_USER, tz_offset_minutes=0, now=_NOW)
    assert queue.total - queue.completed == summary.due_count


@pytest.mark.anyio
async def test_queue_total_minus_completed_equals_summary_due_count() -> None:
    store = InMemoryFlashcards()
    cards = [_card(due_on=_TODAY, source_path_id=None, front=f"c{i}") for i in range(5)]
    for card in cards:
        store.add_card(card)

    await _assert_total_minus_completed_equals_due_count(store)  # nothing graded yet

    await _grade(
        store,
        user_id=_USER,
        card_id=cards[0].id,
        grade=FlashcardGrade.GOT_IT,
        rung_before=_rung(cards[0]),
        tz_offset_minutes=0,
        now=_NOW,
    )
    await _grade(
        store,
        user_id=_USER,
        card_id=cards[1].id,
        grade=FlashcardGrade.GOT_IT,
        rung_before=_rung(cards[1]),
        tz_offset_minutes=0,
        now=_NOW,
    )
    await _assert_total_minus_completed_equals_due_count(store)  # two got_it

    await _grade(
        store,
        user_id=_USER,
        card_id=cards[2].id,
        grade=FlashcardGrade.AGAIN,
        rung_before=_rung(cards[2]),
        tz_offset_minutes=0,
        now=_NOW,
    )
    await _assert_total_minus_completed_equals_due_count(store)  # plus a lapse

    for card in cards[3:]:
        await _grade(
            store,
            user_id=_USER,
            card_id=card.id,
            grade=FlashcardGrade.GOT_IT,
            rung_before=_rung(card),
            tz_offset_minutes=0,
            now=_NOW,
        )
    await _assert_total_minus_completed_equals_due_count(store)  # every card touched


# --------------------------------------------------------------------------- #
# Finding 6: `estimated_minutes` must not round a one-card day down to zero.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_estimated_minutes_is_never_zero_when_a_card_is_due() -> None:
    store = InMemoryFlashcards()
    store.add_card(_card(due_on=_TODAY, source_path_id=None))

    summary = await _load_summary(store, user_id=_USER, tz_offset_minutes=0, now=_NOW)

    # `round(25 / 60) == 0` was the bug ("1 card · ~0 min"); `math.ceil` must
    # never report zero minutes for a non-zero `due_count`.
    assert summary.due_count == 1
    assert summary.estimated_minutes >= 1


# --------------------------------------------------------------------------- #
# grade_card's guards: 404 unowned/unknown, 409 not_due, 409 stale_rung.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_grading_an_unknown_card_is_404() -> None:
    store = InMemoryFlashcards()

    with pytest.raises(HTTPException) as excinfo:
        await _grade(
            store,
            user_id=_USER,
            card_id=uuid.uuid4(),
            grade=FlashcardGrade.GOT_IT,
            rung_before=0,
            tz_offset_minutes=0,
            now=_NOW,
        )
    assert excinfo.value.status_code == 404


@pytest.mark.anyio
async def test_grading_another_learners_card_is_404() -> None:
    store = InMemoryFlashcards()
    other_user_card = _card(due_on=_TODAY, source_path_id=None)
    other_user_card.user_id = uuid.uuid4()
    store.add_card(other_user_card)

    with pytest.raises(HTTPException) as excinfo:
        await _grade(
            store,
            user_id=_USER,
            card_id=other_user_card.id,
            grade=FlashcardGrade.GOT_IT,
            rung_before=_rung(other_user_card),
            tz_offset_minutes=0,
            now=_NOW,
        )
    assert excinfo.value.status_code == 404


@pytest.mark.anyio
async def test_grading_a_card_not_due_today_is_409_not_due() -> None:
    store = InMemoryFlashcards()
    not_due = _card(due_on=_TODAY + timedelta(days=5), source_path_id=None)
    store.add_card(not_due)

    with pytest.raises(HTTPException) as excinfo:
        await _grade(
            store,
            user_id=_USER,
            card_id=not_due.id,
            grade=FlashcardGrade.GOT_IT,
            rung_before=_rung(not_due),
            tz_offset_minutes=0,
            now=_NOW,
        )
    assert excinfo.value.status_code == 409
    assert _conflict_reason(excinfo.value) == "not_due"


@pytest.mark.anyio
async def test_grading_an_already_satisfied_card_again_today_is_409_not_due() -> None:
    store = InMemoryFlashcards()
    card = _card(due_on=_TODAY, source_path_id=None)
    store.add_card(card)
    await _grade(
        store,
        user_id=_USER,
        card_id=card.id,
        grade=FlashcardGrade.GOT_IT,
        rung_before=_rung(card),
        tz_offset_minutes=0,
        now=_NOW,
    )

    with pytest.raises(HTTPException) as excinfo:
        await _grade(
            store,
            user_id=_USER,
            card_id=card.id,
            grade=FlashcardGrade.GOT_IT,
            rung_before=_rung(card),
            tz_offset_minutes=0,
            now=_NOW,
        )
    assert excinfo.value.status_code == 409
    assert _conflict_reason(excinfo.value) == "not_due"


@pytest.mark.anyio
async def test_grading_with_a_stale_rung_before_is_409_stale_rung() -> None:
    store = InMemoryFlashcards()
    card = _card(due_on=_TODAY, rung=2, source_path_id=None)
    store.add_card(card)

    with pytest.raises(HTTPException) as excinfo:
        await _grade(
            store,
            user_id=_USER,
            card_id=card.id,
            grade=FlashcardGrade.GOT_IT,
            rung_before=99,  # stale
            tz_offset_minutes=0,
            now=_NOW,
        )
    assert excinfo.value.status_code == 409
    assert _conflict_reason(excinfo.value) == "stale_rung"
    # No review row appended on a rejected grade.
    assert store.reviews == []


# --------------------------------------------------------------------------- #
# The pin, end to end in memory (§11's own words) — *above* the cap.
#
# Deliberately 11 candidates against the default `flashcard_daily_cap=10`
# (finding #2): a 5-candidate version of this test (`len(candidates) <= cap`)
# never runs the 7/3 split at all — `select_daily_queue` short-circuits to
# "every candidate selected" — so the hash draw, the seed, and (critically)
# the `COALESCE`/"reviewed today" restoration that keeps a graded card pinned
# in place *at the cap* were never exercised by this test. That is exactly
# §15's highest-consequence, lowest-visibility surface: with 11 due, grading a
# card pushes its live `due_on` into the future, and if the pin were wrong the
# graded card would fall out of the population and the 11th would silently
# take its slot — the day rerolling mid-session.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_pin_end_to_end_grade_three_then_re_derive() -> None:
    store = InMemoryFlashcards()
    # A spread of overdue dates so the selection is meaningful (not an
    # all-ties-on-due_on set): the 7 most overdue plus 3 drawn from the rest.
    cards = [
        _card(due_on=_TODAY - timedelta(days=i), source_path_id=None, front=f"c{i}")
        for i in range(11)
    ]
    for card in cards:
        store.add_card(card)

    before = await _queue(store)
    before_ids = [card.card_id for card in before.cards]
    assert before.total == 10  # capped, not eleven — the 7/3 split actually ran
    assert before.completed == 0

    # Grade three `got_it`.
    for card_id in before_ids[:3]:
        source_card = store.cards[card_id]
        await _grade(
            store,
            user_id=_USER,
            card_id=card_id,
            grade=FlashcardGrade.GOT_IT,
            rung_before=_rung(source_card),
            tz_offset_minutes=0,
            now=_NOW,
        )

    after = await _queue(store)
    after_ids = {card.card_id for card in after.cards}
    remaining_ids = set(before_ids[3:])

    # Same ten: the total is unchanged, three are now satisfied, and the
    # still-unsatisfied seven are the same seven, same order — the eleventh
    # candidate has not slid into any of the three graded slots.
    assert after.total == 10
    assert after.completed == 3
    assert after_ids == remaining_ids
    assert [card.card_id for card in after.cards] == before_ids[3:]

    # Grade one of the remaining `again` — it stays in the set (unbounded
    # re-show, D8), but resurfaces *behind* the untouched ones (D8's "later in
    # the session").
    lapsed_id = before_ids[3]
    untouched_ids = before_ids[4:]
    await _grade(
        store,
        user_id=_USER,
        card_id=lapsed_id,
        grade=FlashcardGrade.AGAIN,
        rung_before=_rung(store.cards[lapsed_id]),
        tz_offset_minutes=0,
        now=_NOW,
    )

    final = await _queue(store)
    assert final.total == 10
    assert final.completed == 3  # the lapse is not satisfied
    assert [card.card_id for card in final.cards] == [*untouched_ids, lapsed_id]
