"""The review queue read, its summary, the grade write, and (AL-410/issue #156)
the card list / edit / delete surface (Phase 3 TDD §5.3-§5.4; AL-410 §3).

The `progress_read.py` shape, applied to Retention: module-level frozen views,
one async public function per route taking `session` first, and the real logic
behind a `Protocol`-seamed private function so unit tests substitute a small
in-memory fake rather than a database (CLAUDE.md: fakes over mocks).

**The service is the sole owner of "today."** Exactly Phase 5 D3/`progress_read.py`'s
arithmetic — `today = (now - timedelta(minutes=tz_offset_minutes)).date()` — via
a keyword-only, defaulted `now` test seam (`now or datetime.now(UTC)`). Neither
the repository (which takes an already-resolved `today`) nor
`domains/scheduling` (which takes it as a parameter and derives nothing) ever
computes it themselves.

**One selection, two views.** `GET /reviews/queue` and `GET /reviews/summary`
both read `_select_today`'s output — the candidate population, the pure
`select_daily_queue` draw (D3, TDD §5.1), and the hydration of exactly the
selected ids (§5.3: "derived from the same selection"). Neither route computes
its own population, so the two payloads cannot disagree about which ten cards
today's set is.

**The citation degrades honestly (D12).** A card's source is `"linked"` iff
its source lesson row still exists *and* that row's *live* `generated_at`
still equals the stamp taken at draft time (`source_generated_at`); otherwise
`"degraded"`, carrying only the copied titles. `services/repositories/flashcards.py`
deliberately leaves this judgement undone (a repository reports facts, not the
citation's kind) — `_citation` here is where TDD D12 actually happens.

**Grading is one transaction, five steps (§5.4).** `grade_card` loads the card
`FOR UPDATE` (404 if unowned/unknown), re-derives today's queue and asserts
membership + unsatisfied (409 `not_due`), asserts the optimistic-concurrency
token (409 `stale_rung`), then appends the review row and updates the
projection *from the same* `apply_grade` result
(`FlashcardRepository.append_review_and_project`, itself one write). This
function never commits — exactly `services/lessons.py`'s posture on writes —
so the caller's `session.commit()` is what makes steps 4-5 move together as
the one transaction §5.4 requires.

**Instrumentation (TDD §9).** `grade_card` emits `review_graded` on every
grade, once steps 4-5 have appended/projected — still ahead of the router's
own `session.commit()` (out of this ticket's edit scope), so the event can in
principle precede a commit that then fails. That is a structural consequence
of not touching the router, not a choice made here; the same trade
`services/flashcard_drafting.py::keep_flashcard_drafts` makes for
`flashcards_kept`.

**AL-410 extends this module rather than adding a new one.** The card list,
its inline edit, and its (soft) delete are additions to the same
repository/service pairing that already owns `CitationView`/`_citation`
(D12), the `FlashcardRecord` hydration shape, and the `Protocol` seams — a
separate module would either duplicate D12's judgement or import a private
helper across a module boundary. `load_card_list`/`edit_card`/`delete_card`
take the same posture `grade_card` does: they raise `HTTPException` for
every domain condition (404 unowned/unknown/draft/deleted; 422 a malformed
cursor) rather than a sentinel, so the router stays parse/translate, and they
never commit — the router's `session.commit()` is what makes each one's
single-statement write durable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, Protocol

from fastapi import HTTPException, status

from aleph import events
from aleph.config import settings
from aleph.domains.scheduling import (
    Candidate,
    CardState,
    Grade,
    apply_grade,
    got_it_interval_days,
    select_daily_queue,
)
from aleph.repositories.flashcards import FlashcardRepository, InvalidCursorError

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.domains.scheduling import LadderDays
    from aleph.models import Flashcard, FlashcardGrade, FlashcardReview
    from aleph.repositories.flashcards import CardPage, DueCandidate, FlashcardRecord


# --------------------------------------------------------------------------- #
# The repository seams (CLAUDE.md: fakes over mocks). Two narrow `Protocol`s
# rather than one wide one — the queue/summary read needs only the first two
# methods, and a unit test fake for it should not also have to get grading's
# write right. `GradeStore` repeats `due_candidates` because step 2 of §5.4
# re-derives the same population the `GET` just read; one concrete repository
# (`FlashcardRepository`) satisfies both structurally.
# --------------------------------------------------------------------------- #


class QueueReader(Protocol):
    """The repository capability the queue/summary read needs (§5.3)."""

    async def due_candidates(
        self, *, user_id: uuid.UUID, today: date
    ) -> list[DueCandidate]: ...

    async def cards_by_ids(
        self, *, user_id: uuid.UUID, card_ids: Sequence[uuid.UUID]
    ) -> list[FlashcardRecord]: ...


class GradeStore(Protocol):
    """The repository capability the grade write needs (§5.4)."""

    async def get_card_for_update(
        self, *, user_id: uuid.UUID, card_id: uuid.UUID
    ) -> Flashcard | None: ...

    async def due_candidates(
        self, *, user_id: uuid.UUID, today: date
    ) -> list[DueCandidate]: ...

    async def append_review_and_project(
        self,
        *,
        card_id: uuid.UUID,
        user_id: uuid.UUID,
        grade: FlashcardGrade,
        reviewed_at: datetime,
        local_day: date,
        rung_before: int,
        rung_after: int,
        due_on_before: date,
        due_on_after: date,
    ) -> FlashcardReview: ...


class CardStore(Protocol):
    """The repository capability the card list/edit/delete surface needs
    (AL-410 §2/§3) — narrow, in the `QueueReader`/`GradeStore` style, so a
    fake for the list surface does not also have to get grading or drafting
    right.
    """

    async def list_cards_for_user(
        self,
        *,
        user_id: uuid.UUID,
        limit: int,
        cursor: str | None,
        path_id: uuid.UUID | None,
        query: str | None,
    ) -> CardPage: ...

    async def update_card_text(
        self, *, user_id: uuid.UUID, card_id: uuid.UUID, front: str, back: str
    ) -> FlashcardRecord | None: ...

    async def soft_delete_card(
        self, *, user_id: uuid.UUID, card_id: uuid.UUID
    ) -> bool: ...


# --------------------------------------------------------------------------- #
# Views — what `routers/v1/flashcards.py` translates to wire DTOs.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CitationView:
    """A card's source line, degraded honestly (D12).

    `kind` is `"linked"` iff the source lesson row still exists *and* its live
    `generated_at` still equals the card's `source_generated_at` stamp —
    otherwise `"degraded"`. `lesson_id` is `None` in the degraded case; the
    router maps that onto a DTO with no `lesson_id` field at all (§6), not a
    nullable one.
    """

    kind: Literal["linked", "degraded"]
    lesson_id: uuid.UUID | None
    lesson_title: str
    path_title: str


@dataclass(frozen=True)
class QueueCardView:
    """One card as the review session shows it (§5.3)."""

    card_id: uuid.UUID
    front: str
    back: str
    rung: int
    got_it_interval_days: int
    source: CitationView
    path_id: uuid.UUID | None


@dataclass(frozen=True)
class ReviewQueueView:
    """`GET /reviews/queue`'s composed payload (§5.3).

    `total`/`completed` are always over the **global** selected set, even in a
    filtered (`scope_path_id` set) session — the denominator a display filter
    must never shrink (§5.3's invariant, and a unit test named after it).
    `cards` is the unsatisfied remainder, already filtered to `scope_path_id`
    and already in serve order (`last_reviewed_at` `NULLS FIRST`, `due_on`,
    `card_id` — never-attempted first, then lapses least-recently-seen first,
    D8's "later in the session" with no session object anywhere).
    """

    today: date
    cards: list[QueueCardView]
    total: int
    completed: int
    scope_path_id: uuid.UUID | None
    other_due_count: int


@dataclass(frozen=True)
class PathDueView:
    """One path's share of today's still-unsatisfied cards (§5.3: the chips
    sum to `ReviewSummaryView.due_count`, which is the global remainder, not
    the day's whole set)."""

    path_id: uuid.UUID
    due_count: int


@dataclass(frozen=True)
class ReviewSummaryView:
    """`GET /reviews/summary`'s composed payload (D9) — the same selection as
    the queue, reduced to counts.

    `due_count` is the **unsatisfied remainder** of today's selected set — the
    same population `ReviewQueueView.cards` exposes, not the set's whole size
    (that number is `ReviewQueueView.total`, which never shrinks). This is
    what lets the invariant `queue.total - queue.completed == summary.due_count`
    hold, and what lets `due_count` (and `paths`, below) actually reach zero as
    the learner works through the day (§8: the pill "hidden entirely at zero";
    §15: the *Due today* card disappearing when the set is done).
    `estimated_minutes` is derived from `settings.flashcard_seconds_per_card`
    over that same remainder, nothing stored."""

    today: date
    due_count: int
    estimated_minutes: int
    paths: list[PathDueView]


@dataclass(frozen=True)
class GradeResultView:
    """`POST /reviews`'s composed payload: the card's new projected state,
    straight from the same `apply_grade` result the review row was written
    from."""

    card_id: uuid.UUID
    rung: int
    due_on: date


@dataclass(frozen=True)
class CardListItemView:
    """One kept card on `GET /flashcards` (AL-410 §2).

    `rung` rides along (the ticket's own field list requires it on the DTO)
    but is a **service-layer output, not a rendering instruction**: the plan
    this ticket implements is explicit that the frontend must not display it
    — *rung* is scheduler vocabulary `docs/CONTEXT.md` never gives the
    learner, and a row shows only its `due_on` (`Due in 3 days` / `Due today`
    / `Due yesterday`). `edited_at` is `None` for a card whose text has never
    been changed since it was kept.
    """

    id: uuid.UUID
    front: str
    back: str
    rung: int
    due_on: date
    edited_at: datetime | None
    source: CitationView


@dataclass(frozen=True)
class CardListView:
    """`GET /flashcards`'s composed payload (§2): one page plus its cursor.

    `next_cursor` is `None` once the last page is reached — the client's
    "Load more" affordance disappears rather than firing a request that would
    come back empty.
    """

    cards: list[CardListItemView]
    next_cursor: str | None


@dataclass(frozen=True)
class _SelectedCard:
    """One card of today's selected set, hydrated, with its today-scoped
    grading facts riding along. Internal — never crosses the service boundary.

    ``due_on`` is the candidate's **pinned**, start-of-day value (§5.3) — the
    same one `select_daily_queue` was run against — carried here rather than
    read back off ``record.due_on``, which `apply_grade` may since have
    overwritten to today for a lapsed card. The serve order (`_load_queue`)
    sorts on this field precisely so the one function whose whole claim is
    "reads the pinned value" actually does.
    """

    record: FlashcardRecord
    due_on: date
    satisfied: bool
    last_reviewed_at: datetime | None


# --------------------------------------------------------------------------- #
# The shared selection (D3/§5.3) — the one derivation the queue and the
# summary are both views over.
# --------------------------------------------------------------------------- #


async def _select_today(
    reader: QueueReader,
    *,
    user_id: uuid.UUID,
    tz_offset_minutes: int,
    now: datetime | None,
) -> tuple[date, list[_SelectedCard]]:
    """Resolve "today", read the candidates, run the pure draw, hydrate the draw.

    The D3 invariant itself — that the candidate population and each
    candidate's `due_on` are stable across a day of grading — lives in
    `FlashcardRepository.due_candidates`; this function only trusts it and
    turns its output into the one selected set every route in this module
    reads from.
    """
    resolved_now = now if now is not None else datetime.now(UTC)
    today = (resolved_now - timedelta(minutes=tz_offset_minutes)).date()

    candidates = await reader.due_candidates(user_id=user_id, today=today)
    selected_ids = select_daily_queue(
        [
            Candidate(
                card_id=candidate.card_id,
                due_on=candidate.due_on,
                satisfied=candidate.satisfied,
            )
            for candidate in candidates
        ],
        seed=f"{user_id}:{today}",
        cap=settings.flashcard_daily_cap,
        overdue_slots=settings.flashcard_overdue_slots,
    )
    candidates_by_id = {candidate.card_id: candidate for candidate in candidates}
    records_by_id = {
        record.id: record
        for record in await reader.cards_by_ids(user_id=user_id, card_ids=selected_ids)
    }

    selected = [
        _SelectedCard(
            record=records_by_id[card_id],
            due_on=candidates_by_id[card_id].due_on,
            satisfied=candidates_by_id[card_id].satisfied,
            last_reviewed_at=candidates_by_id[card_id].last_reviewed_at,
        )
        for card_id in selected_ids
        # Defensive only: a card selected from `due_candidates` and then
        # missing from `cards_by_ids` would mean it was deleted between the
        # two reads in the same transaction, which nothing in this design does.
        if card_id in records_by_id
    ]
    return today, selected


def _citation(record: FlashcardRecord) -> CitationView:
    """D12's judgement: linked iff the source lesson still exists and has not
    moved on since this card was drafted."""
    linked = (
        record.source_lesson_id is not None
        and record.current_lesson_generated_at == record.source_generated_at
    )
    if linked:
        return CitationView(
            kind="linked",
            lesson_id=record.source_lesson_id,
            lesson_title=record.source_lesson_title,
            path_title=record.source_path_title,
        )
    return CitationView(
        kind="degraded",
        lesson_id=None,
        lesson_title=record.source_lesson_title,
        path_title=record.source_path_title,
    )


def _card_list_item_view(record: FlashcardRecord) -> CardListItemView:
    """One list row (AL-410 §2), reusing :func:`_citation` (D12) — the same
    judgement the queue makes, applied to the same `FlashcardRecord` shape.
    """
    return CardListItemView(
        id=record.id,
        front=record.front,
        back=record.back,
        rung=record.rung,
        due_on=record.due_on,
        edited_at=record.edited_at,
        source=_citation(record),
    )


def _queue_card_view(card: _SelectedCard, *, ladder: LadderDays) -> QueueCardView:
    """`got_it_interval_days` previews the *Got it* button: the interval a
    `GOT_IT` grade would actually schedule from this card's current rung
    (`domains.scheduling.got_it_interval_days` — the promotion happens inside
    it, so this must not pre-promote via `apply_grade` itself; that mismatch
    was the bug its docstring names)."""
    record = card.record
    return QueueCardView(
        card_id=record.id,
        front=record.front,
        back=record.back,
        rung=record.rung,
        got_it_interval_days=got_it_interval_days(
            CardState(rung=record.rung, due_on=record.due_on), ladder=ladder
        ),
        source=_citation(record),
        path_id=record.source_path_id,
    )


async def _load_queue(
    reader: QueueReader,
    *,
    user_id: uuid.UUID,
    tz_offset_minutes: int,
    path_id: uuid.UUID | None,
    now: datetime | None,
) -> ReviewQueueView:
    today, selected = await _select_today(
        reader, user_id=user_id, tz_offset_minutes=tz_offset_minutes, now=now
    )
    ladder = settings.flashcard_ladder

    total = len(selected)
    completed = sum(1 for card in selected if card.satisfied)

    # Serve order (§5.3): never-attempted first (`last_reviewed_at is None`),
    # then lapses least-recently-seen first — the D8 "later in the session"
    # rule, with no session object anywhere. The bool-first tuple key sorts
    # the `None` group before the timestamped one without ever comparing a
    # `None` to a `datetime` (Python short-circuits on the first differing
    # element, so the `datetime | None` slot is only ever compared within its
    # own group).
    unsatisfied = sorted(
        (card for card in selected if not card.satisfied),
        key=lambda card: (
            card.last_reviewed_at is not None,
            card.last_reviewed_at,
            card.due_on,  # the pinned, start-of-day value — never the live
            # `record.due_on`, which `apply_grade` may have already
            # overwritten to today for a card lapsed earlier in the session.
            card.record.id,
        ),
    )
    all_cards = [_queue_card_view(card, ladder=ladder) for card in unsatisfied]

    if path_id is None:
        return ReviewQueueView(
            today=today,
            cards=all_cards,
            total=total,
            completed=completed,
            scope_path_id=None,
            other_due_count=0,
        )

    # `path_id` filtering is **display only** (§5.3): `total`/`completed` above
    # are already computed over the global set and are untouched by this
    # branch. `other_due_count` is what makes the end-of-filtered-session widen
    # offer possible (PRD §4.10) — how many unsatisfied cards exist outside
    # this path's scope.
    scoped = [card for card in all_cards if card.path_id == path_id]
    return ReviewQueueView(
        today=today,
        cards=scoped,
        total=total,
        completed=completed,
        scope_path_id=path_id,
        other_due_count=len(all_cards) - len(scoped),
    )


async def _load_summary(
    reader: QueueReader,
    *,
    user_id: uuid.UUID,
    tz_offset_minutes: int,
    now: datetime | None,
) -> ReviewSummaryView:
    today, selected = await _select_today(
        reader, user_id=user_id, tz_offset_minutes=tz_offset_minutes, now=now
    )

    # `due_count` (and everything derived from it below) is over the
    # **unsatisfied remainder** only — the same population `_load_queue`
    # exposes as `cards` — never the whole day's selected set. `total` (the
    # `of 10` denominator) stays `len(selected)` in `_load_queue`; this is the
    # one place that must NOT reuse that number, or `due_count` can never
    # reach zero through work and the invariant
    # `queue.total - queue.completed == summary.due_count` fails as soon as a
    # single card is graded `got_it`.
    unsatisfied = [card for card in selected if not card.satisfied]
    due_count = len(unsatisfied)

    # Per-path counts over the unsatisfied remainder only, for the same reason
    # — each path's *remaining* share of today's ten, summing to `due_count`
    # by construction (§5.3). An orphaned card (`source_path_id` is `None`,
    # D12) contributes to the global count but no path row.
    counts_by_path: dict[uuid.UUID, int] = {}
    for card in unsatisfied:
        source_path_id = card.record.source_path_id
        if source_path_id is None:
            continue
        counts_by_path[source_path_id] = counts_by_path.get(source_path_id, 0) + 1

    paths = [
        PathDueView(path_id=path_id, due_count=count)
        for path_id, count in sorted(
            counts_by_path.items(), key=lambda item: str(item[0])
        )
    ]

    # `math.ceil`, not `round`: a one-card day rounds `25/60` down to 0 with
    # `round`, which reads as "1 card · ~0 min" — nonsensical for a non-zero
    # `due_count`. `due_count == 0` stays exactly `0` (nothing to ceil up to).
    estimated_minutes = (
        math.ceil(due_count * settings.flashcard_seconds_per_card / 60)
        if due_count > 0
        else 0
    )

    return ReviewSummaryView(
        today=today,
        due_count=due_count,
        estimated_minutes=estimated_minutes,
        paths=paths,
    )


# --------------------------------------------------------------------------- #
# Grading (§5.4) — one transaction, five steps.
# --------------------------------------------------------------------------- #


def _not_found() -> HTTPException:
    """A `404` for a card the caller does not own or that does not exist.

    404-never-403 (§5.6, the posture every router in this codebase takes): a
    learner cannot tell "not yours" from "does not exist."
    """
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="card not found")


def _conflict(reason: str, message: str) -> HTTPException:
    """A `409` through the shared envelope, carrying its reason beside a
    human sentence — the `routers/v1/shaping.py` `_conflict_reason` shape
    (`app.py`'s handler promotes `message` out of the mapping; `code` stays
    `conflict` for every `409` in this app, and `details.reason` is what a
    client actually branches on, per §5.6's wire table).
    """
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"reason": reason, "message": message},
    )


async def _grade(
    store: GradeStore,
    *,
    user_id: uuid.UUID,
    card_id: uuid.UUID,
    grade: FlashcardGrade,
    rung_before: int,
    tz_offset_minutes: int,
    now: datetime | None,
) -> GradeResultView:
    resolved_now = now if now is not None else datetime.now(UTC)
    today = (resolved_now - timedelta(minutes=tz_offset_minutes)).date()

    # Step 1: load `FOR UPDATE`, scoped by `user_id` on the row itself (404).
    card = await store.get_card_for_update(user_id=user_id, card_id=card_id)
    if card is None:
        raise _not_found()

    # Step 2: re-derive today's queue (the same derivation the `GET` just ran)
    # and assert the card is in it and unsatisfied (409 `not_due`). This is
    # what stops a learner grading a card that is not today's business, and
    # covers both "never selected today" and "already satisfied today" — a
    # second `got_it` on the same card is not today's business either.
    candidates = await store.due_candidates(user_id=user_id, today=today)
    selected_ids = set(
        select_daily_queue(
            [
                Candidate(
                    card_id=candidate.card_id,
                    due_on=candidate.due_on,
                    satisfied=candidate.satisfied,
                )
                for candidate in candidates
            ],
            seed=f"{user_id}:{today}",
            cap=settings.flashcard_daily_cap,
            overdue_slots=settings.flashcard_overdue_slots,
        )
    )
    candidate = next((c for c in candidates if c.card_id == card_id), None)
    if candidate is None or card_id not in selected_ids or candidate.satisfied:
        raise _conflict(
            "not_due", "this card is not part of today's review set right now."
        )

    # Step 3: optimistic concurrency on the projection (409 `stale_rung`).
    # This absorbs a double-tapped button or a retried request whenever the
    # grade actually moves the rung: `GOT_IT` always promotes, and `AGAIN`
    # demotes at every rung above 0, so the retry's `rung_before` is already
    # stale on the second attempt and gets rejected as a no-op.
    #
    # The one case this does NOT absorb: a double-tapped `AGAIN` on a card
    # already at rung 0. `apply_grade` floors there, so `rung_after ==
    # rung_before == 0` — the guard sees no change and lets the second request
    # through — and step 2's `satisfied` check never trips either, since
    # `AGAIN` never satisfies a card. A genuine duplicate request therefore
    # appends a second review row. This is a real limit, not a bug to fix:
    # nothing in the request distinguishes a resubmitted double-tap from an
    # honest second lapse on the same card in one sitting, which D8's
    # unbounded re-show explicitly allows. Since D1 makes the log
    # authoritative, the visible consequence is an over-count in §9's *recall
    # rate by rung* specifically at rung 0, not a scheduling error.
    if card.rung != rung_before:
        raise _conflict(
            "stale_rung", "this card has already moved on since you last saw it."
        )

    # A card that passed step 2 necessarily has `kept_at` set (`due_candidates`
    # scopes to kept cards only, §5.3's SQL), so `rung`/`due_on` are populated —
    # narrowed for the type checker rather than re-deriving what step 2 already
    # proved.
    assert card.rung is not None
    assert card.due_on is not None

    ladder = settings.flashcard_ladder
    parsed_grade = Grade(grade.value)
    state_before = CardState(rung=card.rung, due_on=card.due_on)
    # Captured *before* the write, like `keep_flashcard_drafts` does: reading an
    # ORM column off `card` after the projection update would depend on the bulk
    # UPDATE's session-synchronization leaving this particular attribute loaded
    # (it does today only because `source_path_id` is not in the SET clause). A
    # scalar taken up front cannot be expired into a lazy refresh that would
    # `MissingGreenlet` out of an otherwise valid grade.
    source_path_id = card.source_path_id
    state_after = apply_grade(state_before, parsed_grade, today=today, ladder=ladder)

    # Steps 4-5: append the review row and update the projection from the
    # *same* `apply_grade` result, in the caller's still-open transaction
    # (`FlashcardRepository.append_review_and_project` flushes but does not
    # commit — the router's `session.commit()` is what makes this one atomic
    # write, D1's "must move together").
    await store.append_review_and_project(
        card_id=card_id,
        user_id=user_id,
        grade=grade,
        reviewed_at=resolved_now,
        local_day=today,
        rung_before=state_before.rung,
        rung_after=state_after.rung,
        due_on_before=state_before.due_on,
        due_on_after=state_after.due_on,
    )

    # `review_graded` (TDD §9) — emitted here, ahead of the router's own
    # `session.commit()` (out of this ticket's edit scope), the same
    # structural trade `keep_flashcard_drafts` makes. `queue_size`/
    # `queue_remaining` are what let a session start/finish be derived from
    # this event alone (no session event, TDD §9): `queue_size` is today's
    # whole selected set (step 2's `selected_ids`, the same population
    # `ReviewQueueView.total` exposes); `queue_remaining` is the unsatisfied
    # count *after* this grade — one fewer than before on a `GOT_IT` (it
    # always satisfies), unchanged on an `AGAIN` (D8: a lapse never
    # satisfies). Both are read off step 2's already-fetched `candidates`, so
    # this costs no extra query.
    queue_size = len(selected_ids)
    unsatisfied_before = sum(
        1 for c in candidates if c.card_id in selected_ids and not c.satisfied
    )
    queue_remaining = (
        unsatisfied_before - 1 if parsed_grade is Grade.GOT_IT else unsatisfied_before
    )
    events.emit_review_graded(
        account_id=user_id,
        card_id=card_id,
        path_id=source_path_id,
        grade=grade.value,
        rung_before=rung_before,
        queue_size=queue_size,
        queue_remaining=queue_remaining,
    )

    return GradeResultView(
        card_id=card_id, rung=state_after.rung, due_on=state_after.due_on
    )


# --------------------------------------------------------------------------- #
# Card management (AL-410 / issue #156, §2/§3): browse, edit, delete.
# --------------------------------------------------------------------------- #


async def _load_cards(
    store: CardStore,
    *,
    user_id: uuid.UUID,
    limit: int,
    cursor: str | None,
    path_id: uuid.UUID | None,
    query: str | None,
) -> CardListView:
    """`GET /flashcards`'s whole read (§2): delegate to the repository's page,
    translate its `FlashcardRecord`s through :func:`_card_list_item_view`.

    Catches :class:`~aleph.repositories.flashcards.InvalidCursorError` and
    re-raises it as a `422` — never a `500` — so a stale or hand-edited
    `cursor` reads as bad input, not a server failure. This is the one place
    that translation happens: the repository only knows "this does not parse"
    and the router only knows "parse the query params," so the service is
    where a parsing failure becomes an HTTP status (the same posture `_grade`
    takes turning a domain condition into `HTTPException`).
    """
    try:
        page = await store.list_cards_for_user(
            user_id=user_id, limit=limit, cursor=cursor, path_id=path_id, query=query
        )
    except InvalidCursorError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="cursor is malformed or has expired",
        ) from exc
    return CardListView(
        cards=[_card_list_item_view(record) for record in page.cards],
        next_cursor=page.next_cursor,
    )


async def _edit_card(
    store: CardStore,
    *,
    user_id: uuid.UUID,
    card_id: uuid.UUID,
    front: str,
    back: str,
) -> CardListItemView:
    """`PATCH /flashcards/{card_id}` (§3): `404` (`_not_found`) when the
    repository's `update_card_text` returns `None` — unowned, unknown, a
    draft, or already-deleted, the same four-way `404` every other ownership
    read in this module gives. Does not commit; the router does, once.
    """
    record = await store.update_card_text(
        user_id=user_id, card_id=card_id, front=front, back=back
    )
    if record is None:
        raise _not_found()
    return _card_list_item_view(record)


async def _delete_card(
    store: CardStore, *, user_id: uuid.UUID, card_id: uuid.UUID
) -> None:
    """`DELETE /flashcards/{card_id}` (§1/§3): `404` when the repository's
    `soft_delete_card` returns `False` — including an already-deleted card,
    which is what makes a double-tapped delete an honest `404` rather than a
    silent second success. Does not commit; the router does, once.
    """
    deleted = await store.soft_delete_card(user_id=user_id, card_id=card_id)
    if not deleted:
        raise _not_found()


# --------------------------------------------------------------------------- #
# Production entry points — build the real repository, delegate the logic.
# --------------------------------------------------------------------------- #


async def load_review_summary(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tz_offset_minutes: int,
    now: datetime | None = None,
) -> ReviewSummaryView:
    """`GET /reviews/summary`'s whole payload (D9/§6)."""
    return await _load_summary(
        FlashcardRepository(session),
        user_id=user_id,
        tz_offset_minutes=tz_offset_minutes,
        now=now,
    )


async def load_review_queue(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tz_offset_minutes: int,
    path_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> ReviewQueueView:
    """`GET /reviews/queue`'s whole payload (§5.3/§6)."""
    return await _load_queue(
        FlashcardRepository(session),
        user_id=user_id,
        tz_offset_minutes=tz_offset_minutes,
        path_id=path_id,
        now=now,
    )


async def grade_card(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    card_id: uuid.UUID,
    grade: FlashcardGrade,
    rung_before: int,
    tz_offset_minutes: int,
    now: datetime | None = None,
) -> GradeResultView:
    """`POST /reviews`'s grading transaction (§5.4). Raises `HTTPException`
    (404 unowned/unknown card, 409 `not_due`/`stale_rung`) rather than
    returning a sentinel — the `services/shaping.py` posture, so the router
    stays parse/translate with no error-shape branching of its own. Does not
    commit; the caller does, once, after this returns.
    """
    return await _grade(
        FlashcardRepository(session),
        user_id=user_id,
        card_id=card_id,
        grade=grade,
        rung_before=rung_before,
        tz_offset_minutes=tz_offset_minutes,
        now=now,
    )


async def load_card_list(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    limit: int,
    cursor: str | None = None,
    path_id: uuid.UUID | None = None,
    query: str | None = None,
) -> CardListView:
    """`GET /flashcards`'s whole payload (AL-410 §2). Raises `HTTPException`
    (422 malformed cursor) rather than returning a sentinel, the same posture
    `grade_card` takes.
    """
    return await _load_cards(
        FlashcardRepository(session),
        user_id=user_id,
        limit=limit,
        cursor=cursor,
        path_id=path_id,
        query=query,
    )


async def edit_card(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    card_id: uuid.UUID,
    front: str,
    back: str,
) -> CardListItemView:
    """`PATCH /flashcards/{card_id}`'s whole write (AL-410 §3): `404`
    unowned/unknown/draft/deleted. Does not commit; the caller does, once,
    after this returns.
    """
    return await _edit_card(
        FlashcardRepository(session),
        user_id=user_id,
        card_id=card_id,
        front=front,
        back=back,
    )


async def delete_card(
    session: AsyncSession, *, user_id: uuid.UUID, card_id: uuid.UUID
) -> None:
    """`DELETE /flashcards/{card_id}`'s whole write (AL-410 §1/§3): `404`
    unowned/unknown/already-deleted. Does not commit; the caller does, once,
    after this returns.
    """
    await _delete_card(FlashcardRepository(session), user_id=user_id, card_id=card_id)
