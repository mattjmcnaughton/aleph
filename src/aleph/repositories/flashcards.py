"""Data access for flashcards: the queue read, grading, drafting, and card
management (TDD §5/§4; AL-410/issue #156 §2).

Every query and write **Phase 3** needed lived here, and downstream *Phase 3*
tickets were forbidden from adding methods to this module — but that was a
**within-phase** rule: the TDD's delivery plan wanted the surface complete
before ticket 5 shipped, not grown piecemeal mid-phase, and once Phase 3
shipped that rule had nothing left to enforce. AL-410 is a **post-phase
amendment**, not a ticket squeezed inside the same window, and it adds methods
here rather than working around the old wording or standing up a second
module: ``list_cards_for_user``/``update_card_text``/``soft_delete_card`` are
plain Flashcard-table access, exactly what every method above them already is,
and centralizing every Flashcard-table read/write in one repository is the
property worth keeping — not the closed-door framing that used to protect it.

**This module must not import :mod:`aleph.domains.scheduling`.** The pure
ladder and the daily selection are a concurrently-written module's business;
this layer only stores already-computed before/after values and runs
already-decided SQL (repositories -> models, never repositories -> domains,
mirroring how ``repositories/lessons.py`` never decides progression, only
records it).

Constructed per-request with the caller's :class:`AsyncSession` (repository
convention); nothing here commits — the service layer owns the transaction,
which is what lets a grade's review-append and projection-update
(:meth:`FlashcardRepository.append_review_and_project`) and a lesson's
keep/discard (:meth:`FlashcardRepository.keep_drafts`) each be one atomic unit
(§5.4 / §5.2).
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import Date, cast, delete, func, literal, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aleph.config import settings
from aleph.models import (
    Flashcard,
    FlashcardDraftRun,
    FlashcardDraftRunState,
    FlashcardGrade,
    FlashcardReview,
    Lesson,
)
from aleph.repositories._generation import (
    affected_rows,
    claimable_predicate,
    effective_state_case,
)

# ``uuid``/``datetime`` are real (not ``TYPE_CHECKING``-only) imports: AL-410's
# cursor codec (:func:`_parse_cursor`/:func:`_build_cursor`, below) parses and
# formats both at runtime, unlike every other name in this module, which is
# used in annotations alone under ``from __future__ import annotations``.

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class DueCandidate:
    """One row of §5.3's candidate query — the daily selection's raw input.

    ``due_on`` is the card's value **as of the start of ``today``** (§5.1's
    ``Candidate.due_on`` docstring), not its live value — see
    :meth:`FlashcardRepository.due_candidates` for the invariant this rests on.
    ``satisfied`` is whether the card's most recent review *today* was
    ``got_it`` (D8's lapse semantics); ``last_reviewed_at`` is ``None`` for a
    card never reviewed today, which is what the serve-order fold
    (never-attempted before lapsed) keys off.
    """

    card_id: uuid.UUID
    due_on: date
    satisfied: bool
    last_reviewed_at: datetime.datetime | None


@dataclass(frozen=True)
class FlashcardRecord:
    """One **kept** card's full detail — the queue's hydration read (§5.3),
    shared verbatim with AL-410's card list (§2).

    ``current_lesson_generated_at`` is the *live* value of the source lesson's
    ``generated_at`` (``None`` when ``source_lesson_id`` is ``None`` — the FK
    already went ``SET NULL``, or the lesson never had generated content).
    Deciding "linked" vs "degraded" (D12) is a service-layer judgement — comparing
    this to ``source_generated_at`` — deliberately left undone here: a
    repository reports facts, not the citation's kind.

    ``kept_at``/``edited_at`` (AL-410) ride along on every read of this shape,
    not only the list's — one hydration shape for both reads beats a second,
    near-identical dataclass that drifts from this one's field-for-field. The
    queue simply ignores both; the list needs ``kept_at`` for its keyset
    cursor and ``edited_at`` for display. Both are guaranteed non-``NULL``
    (``kept_at``) or meaningfully ``NULL`` (``edited_at``, "never edited") by
    every query that produces this record, since each one already filters to
    ``kept_at IS NOT NULL``.
    """

    id: uuid.UUID
    front: str
    back: str
    rung: int
    due_on: date
    kept_at: datetime.datetime
    edited_at: datetime.datetime | None
    source_lesson_id: uuid.UUID | None
    source_path_id: uuid.UUID | None
    source_lesson_title: str
    source_path_title: str
    source_generated_at: datetime.datetime
    current_lesson_generated_at: datetime.datetime | None


# --------------------------------------------------------------------------- #
# AL-410 §2: the card list's page-size ceiling, the keyset cursor codec, and
# the ``ILIKE`` escaper. Module-level because none of the three touches
# ``self`` — the cursor codec in particular is reused nowhere near the ORM.
# --------------------------------------------------------------------------- #

# Enforced here as well as at the DTO/query-param layer
# (``dtos/flashcards.py``'s ``Query(..., le=50)``) — defense in depth for a
# caller that reaches this repository directly, bypassing the router.
MAX_CARD_LIST_LIMIT = 50


class InvalidCursorError(ValueError):
    """Raised by :func:`_parse_cursor` on a ``cursor`` that does not decode as
    ``"{kept_at_isoformat}|{card_id}"`` (§2) — opaque to the client by
    construction, so any other shape is exactly as malformed as a
    hand-crafted one. Caught by :mod:`aleph.services.reviews`, which turns it
    into a ``422`` (never a ``500``): a stale or hand-edited cursor is bad
    input, not a server failure.
    """


@dataclass(frozen=True)
class CardPage:
    """One page of :meth:`FlashcardRepository.list_cards_for_user`, plus its
    cursor (§2). ``next_cursor`` is ``None`` once the last page is reached —
    the client's own signal to stop offering "Load more" rather than firing a
    request that would come back empty.
    """

    cards: list[FlashcardRecord]
    next_cursor: str | None


def _parse_cursor(cursor: str) -> tuple[datetime.datetime, uuid.UUID]:
    """Decode ``"{kept_at_isoformat}|{card_id}"`` -> ``(kept_at, card_id)``.

    Raises :class:`InvalidCursorError` on anything else — a missing
    separator, an unparseable timestamp, or a non-UUID id — rather than
    letting the underlying ``ValueError`` from ``datetime.fromisoformat``/
    ``uuid.UUID`` propagate under its own name: every caller then has exactly
    one exception type to catch for "this cursor is garbage."
    """
    try:
        kept_at_raw, card_id_raw = cursor.split("|", 1)
        return datetime.datetime.fromisoformat(kept_at_raw), uuid.UUID(card_id_raw)
    except ValueError as exc:
        raise InvalidCursorError(f"malformed cursor: {cursor!r}") from exc


def _build_cursor(kept_at: datetime.datetime, card_id: uuid.UUID) -> str:
    """The inverse of :func:`_parse_cursor` — opaque to the client either way."""
    return f"{kept_at.isoformat()}|{card_id}"


def _escape_like(needle: str) -> str:
    """Escape ``%``/``_``/the escape character itself, for a literal ``ILIKE``.

    Order matters: the escape character (``\\``) is escaped *first*, or
    escaping ``%``/``_`` afterwards would double-escape the backslashes those
    two steps just introduced. Without this, a learner searching for a card
    containing a literal ``%`` would have it silently read as an ``ILIKE``
    wildcard and match every card (§2's own stated risk).
    """
    return needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class FlashcardRepository:
    """Data access for :class:`~aleph.models.Flashcard` and its two sibling tables.

    ``stale_after_seconds`` is the drafting stale window (TDD §5.2: "on the
    Phase 1 §5.4 timings") backing the poll's stale-aware read
    (:meth:`get_effective_draft_run_state`) — the ``LessonRepository``/
    ``PathRepository`` precedent. It defaults to the configured generation
    window but a caller may inject a different one, exactly as those two
    repositories allow. ``claim_draft_run`` is unaffected: it already takes
    its own ``stale_after_seconds`` as an explicit call argument (D7, complete
    before this ticket) and does not read ``self._stale_after_seconds``.
    """

    def __init__(
        self, session: AsyncSession, *, stale_after_seconds: float | None = None
    ) -> None:
        self.session = session
        self._stale_after_seconds = (
            stale_after_seconds
            if stale_after_seconds is not None
            else settings.generation_stale_after_seconds
        )

    def _effective_draft_run_state_expr(self) -> ColumnElement[str]:
        return effective_state_case(
            state_col=FlashcardDraftRun.state,
            started_at_col=FlashcardDraftRun.started_at,
            generating_state=FlashcardDraftRunState.GENERATING,
            failed_state=FlashcardDraftRunState.FAILED,
            stale_after_seconds=self._stale_after_seconds,
        )

    # -- the queue read (§5.3) ---------------------------------------------- #

    async def due_candidates(
        self, *, user_id: uuid.UUID, today: date
    ) -> list[DueCandidate]:
        """The candidate population for ``today``'s queue, one row per card.

        Implements §5.3's SQL exactly: two ``DISTINCT ON`` CTEs over *today's*
        reviews (``first_today`` — each card's ``due_on_before`` as of its
        first review today; ``latest_today`` — each card's most recent grade
        and timestamp today), left-joined onto every one of the learner's kept
        cards, filtered to ``due_on <= today OR reviewed today``.

        **The invariant this exists to prove (D3), stated once and pinned by
        ``tests/integration/test_flashcards_schema.py``:** for a fixed
        ``(user_id, today)``, the candidate set and each candidate's ``due_on``
        are **invariant under grading**. Grading moves ``flashcards.due_on``
        into the future, which would drop the card out of the ``due_on <=
        :today`` arm — but the ``ft.card_id IS NOT NULL`` arm (a review exists
        today) puts it straight back in, and ``COALESCE(ft.due_on_before,
        f.due_on)`` restores the value the card had *before* today's grade.
        Because the daily selection (:mod:`aleph.domains.scheduling`,
        ``select_daily_queue``) is a pure function of this set, the day's ten
        are the same ten — in the same order — on every request of the day,
        with no queue table anywhere (D3).

        Two things fall outside the invariant, recorded rather than defended
        (§5.3): a card **kept** later in the day never joins today's set (it is
        due tomorrow — the desired behaviour), and nothing in this design
        writes ``due_on`` except a review.

        **Excludes soft-deleted cards** (``deleted_at IS NOT NULL``, AL-410):
        correctness-critical, not defensive — miss this and a deleted card
        keeps appearing in the daily queue it was removed from.
        """
        # ``reviewed_at`` alone does not total-order two reviews of the same
        # card written in the same instant (a double-tap racing the
        # ``rung_before`` guard, or two rows sharing a clock tick) — ``id`` is
        # the tie-break so ``DISTINCT ON`` can never pick between them
        # arbitrarily.
        first_today = (
            select(FlashcardReview.card_id, FlashcardReview.due_on_before)
            .where(
                FlashcardReview.user_id == user_id,
                FlashcardReview.local_day == today,
            )
            .distinct(FlashcardReview.card_id)
            .order_by(
                FlashcardReview.card_id,
                FlashcardReview.reviewed_at.asc(),
                FlashcardReview.id.asc(),
            )
            .cte("first_today")
        )
        latest_today = (
            select(
                FlashcardReview.card_id,
                FlashcardReview.grade,
                FlashcardReview.reviewed_at,
            )
            .where(
                FlashcardReview.user_id == user_id,
                FlashcardReview.local_day == today,
            )
            .distinct(FlashcardReview.card_id)
            .order_by(
                FlashcardReview.card_id,
                FlashcardReview.reviewed_at.desc(),
                FlashcardReview.id.desc(),
            )
            .cte("latest_today")
        )

        due_on_expr = func.coalesce(first_today.c.due_on_before, Flashcard.due_on)
        satisfied_expr = latest_today.c.grade == FlashcardGrade.GOT_IT

        result = await self.session.execute(
            select(
                Flashcard.id,
                due_on_expr.label("due_on"),
                satisfied_expr.label("satisfied"),
                latest_today.c.reviewed_at.label("last_reviewed_at"),
            )
            .select_from(Flashcard)
            .outerjoin(first_today, first_today.c.card_id == Flashcard.id)
            .outerjoin(latest_today, latest_today.c.card_id == Flashcard.id)
            .where(
                Flashcard.user_id == user_id,
                Flashcard.kept_at.isnot(None),
                Flashcard.deleted_at.is_(None),
                or_(Flashcard.due_on <= today, first_today.c.card_id.isnot(None)),
            )
        )
        return [
            DueCandidate(
                card_id=row.id,
                due_on=row.due_on,
                satisfied=bool(row.satisfied),
                last_reviewed_at=row.last_reviewed_at,
            )
            for row in result.all()
        ]

    async def cards_by_ids(
        self, *, user_id: uuid.UUID, card_ids: Sequence[uuid.UUID]
    ) -> list[FlashcardRecord]:
        """Hydrate the display fields for a set of **kept** card ids.

        Scoped by ``user_id`` on the row itself (§4 item 3): an id that is not
        this learner's kept card is silently absent from the result rather than
        raising, mirroring ``LessonRepository.get_for_user``'s posture.
        Left-joins the source lesson (if it still exists) to expose its *live*
        ``generated_at`` — see :class:`FlashcardRecord`.

        **Does not preserve ``card_ids``' input order.** This is a plain ``IN``
        select — the database returns rows in whatever order it finds them, not
        in ``card_ids`` order — so a caller that needs a specific order (the
        queue's serve order, §5.3) must re-sort the result against the
        selection that produced ``card_ids`` in the first place.

        **Excludes soft-deleted cards** (``deleted_at IS NOT NULL``, AL-410),
        correctness-critical for the same reason :meth:`due_candidates` filters
        it: a deleted id passed in here (e.g. hydrating a stale selection) must
        not silently hydrate back into a queue.
        """
        if not card_ids:
            return []
        result = await self.session.execute(
            select(
                Flashcard.id,
                Flashcard.front,
                Flashcard.back,
                Flashcard.rung,
                Flashcard.due_on,
                Flashcard.kept_at,
                Flashcard.edited_at,
                Flashcard.source_lesson_id,
                Flashcard.source_path_id,
                Flashcard.source_lesson_title,
                Flashcard.source_path_title,
                Flashcard.source_generated_at,
                Lesson.generated_at.label("current_lesson_generated_at"),
            )
            .select_from(Flashcard)
            .outerjoin(Lesson, Lesson.id == Flashcard.source_lesson_id)
            .where(
                Flashcard.user_id == user_id,
                Flashcard.id.in_(card_ids),
                Flashcard.kept_at.isnot(None),
                Flashcard.deleted_at.is_(None),
            )
        )
        return [
            FlashcardRecord(
                id=row.id,
                front=row.front,
                back=row.back,
                rung=row.rung,
                due_on=row.due_on,
                kept_at=row.kept_at,
                edited_at=row.edited_at,
                source_lesson_id=row.source_lesson_id,
                source_path_id=row.source_path_id,
                source_lesson_title=row.source_lesson_title,
                source_path_title=row.source_path_title,
                source_generated_at=row.source_generated_at,
                current_lesson_generated_at=row.current_lesson_generated_at,
            )
            for row in result.all()
        ]

    # -- grading (§5.4) ------------------------------------------------------ #

    async def get_card_for_update(
        self, *, user_id: uuid.UUID, card_id: uuid.UUID
    ) -> Flashcard | None:
        """Load one card ``FOR UPDATE``, scoped by ``user_id`` on the row itself.

        The grading transaction's first step (§5.4 #1): the row lock is what
        makes the ``rung_before`` optimistic-concurrency check
        (:mod:`aleph.services.reviews`, not this layer) race-free against a
        double-tapped grade. ``None`` for an unowned or unknown id — 404, never
        403 (§4 item 3, the same posture as every other ownership read here).

        **Also excludes a soft-deleted card** (``deleted_at IS NOT NULL``,
        AL-410) — deliberately, not incidentally. Without this filter, grading
        a deleted card would still load the row here, fail the *next* guard
        instead (:mod:`aleph.services.reviews`'s ``due_candidates`` re-derivation,
        which already excludes deleted cards), and surface as ``409 not_due`` —
        a misleading answer for a card that was never "not due today" but
        simply gone. Filtering here makes it ``404`` instead, which is what the
        acceptance criteria require and the only answer a deleted card should
        ever produce.
        """
        result = await self.session.execute(
            select(Flashcard)
            .where(
                Flashcard.id == card_id,
                Flashcard.user_id == user_id,
                Flashcard.deleted_at.is_(None),
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def append_review_and_project(
        self,
        *,
        card_id: uuid.UUID,
        user_id: uuid.UUID,
        grade: FlashcardGrade,
        reviewed_at: datetime.datetime,
        local_day: date,
        rung_before: int,
        rung_after: int,
        due_on_before: date,
        due_on_after: date,
    ) -> FlashcardReview:
        """Append the review row and update the projection, in one transaction.

        Every value is **already computed** by the caller (the service, from
        :func:`aleph.domains.scheduling.apply_grade`) — this method decides
        nothing about scheduling, only writes what it is told (D1/§5.4 step
        4-5: "steps 4 and 5 are one transaction and must move together"). The
        caller commits; this only ``flush``es so the returned row's ``id`` is
        populated.

        The projection ``UPDATE`` is scoped by both ``id`` and ``user_id`` —
        belt-and-braces, since :meth:`get_card_for_update` already proved
        ownership earlier in the same transaction.
        """
        review = FlashcardReview(
            card_id=card_id,
            user_id=user_id,
            grade=grade,
            reviewed_at=reviewed_at,
            local_day=local_day,
            rung_before=rung_before,
            rung_after=rung_after,
            due_on_before=due_on_before,
            due_on_after=due_on_after,
        )
        self.session.add(review)
        await self.session.execute(
            update(Flashcard)
            .where(Flashcard.id == card_id, Flashcard.user_id == user_id)
            .values(rung=rung_after, due_on=due_on_after, updated_at=func.now())
        )
        await self.session.flush()
        return review

    async def reviews_for_card(
        self, *, user_id: uuid.UUID, card_id: uuid.UUID
    ) -> list[FlashcardReview]:
        """One card's full review history, oldest first — the replay test's read.

        Ordered by ``reviewed_at`` (then ``id`` to totally order two reviews
        sharing a timestamp) so folding ``apply_grade`` over the result
        reproduces the card's current ``rung``/``due_on`` exactly (D1's replay
        property, TDD §11).

        Scoped by ``user_id`` on the row itself, like every other read in this
        module (§4 item 3) — unlike :meth:`get_card_for_update`'s ``None``
        posture, an id that is not this learner's card returns an empty list
        rather than another learner's history, since there is no ownership
        question to signal a 404 for here (the caller already holds a card it
        proved ownership of).
        """
        result = await self.session.execute(
            select(FlashcardReview)
            .where(
                FlashcardReview.card_id == card_id,
                FlashcardReview.user_id == user_id,
            )
            .order_by(FlashcardReview.reviewed_at, FlashcardReview.id)
        )
        return list(result.scalars())

    # -- the streak union (§5.5, D11) ---------------------------------------- #

    async def review_days_for_user(
        self, *, user_id: uuid.UUID, tz_offset_minutes: int
    ) -> list[date]:
        """Every distinct local day this learner reviewed a card, for the streak union.

        Implements §5.5's SQL exactly, and recomputes the day from
        ``reviewed_at`` rather than reading the stored ``local_day`` — the D11
        distinction: ``local_day`` is a *scheduling* fact frozen at write time,
        this is a *streak* fact, and the two must not be conflated (see
        :class:`~aleph.models.FlashcardReview`'s docstring).

        The expression is copied from
        ``LessonRepository.completion_days_for_user`` (Phase 5 TDD §5.2) verbatim
        — ``func.timezone("UTC", ...)`` pins to UTC *before* subtracting the
        offset, so the result does not depend on the session's ``TimeZone`` GUC.
        ``tests/integration/test_flashcards_schema.py`` pins this under ``SET
        TIME ZONE 'America/Chicago'``, mirroring Phase 5's own guard.
        """
        local_day = cast(
            func.timezone("UTC", FlashcardReview.reviewed_at)
            - func.make_interval(0, 0, 0, 0, 0, tz_offset_minutes),
            Date,
        )
        result = await self.session.execute(
            select(local_day.label("day"))
            .where(FlashcardReview.user_id == user_id)
            .distinct()
        )
        return list(result.scalars())

    # -- drafting: claim / resolve (D7, TDD §5.2) ----------------------------- #

    async def claim_draft_run(
        self, *, lesson_id: uuid.UUID, stale_after_seconds: float
    ) -> datetime.datetime | None:
        """Claim (or re-claim) ``lesson_id``'s draft run; the D7 claim protocol.

        ``INSERT ... ON CONFLICT (lesson_id) DO UPDATE ... WHERE <claimable>``
        (§5.2 #2, TDD §11), where *claimable* is
        :func:`aleph.repositories._generation.claimable_predicate` — the same
        predicate ``LessonRepository`` uses, reused rather than re-derived so
        the two claim protocols cannot drift apart: a lesson drafted for the
        first time wins on the plain insert; a re-``POST`` while ``generating``
        **or already ``generated``** matches no update arm and wins nothing (a
        structurally impossible double-draft — ``generated`` is terminal and is
        never in ``claimable_states``, so it is never re-claimed no matter how
        old it is); a ``failed`` run, or a ``generating`` one whose
        ``started_at`` is older than the stale window, is re-claimable.

        ``stale_after_seconds`` is a value, not a precomputed cutoff: the
        comparison (``started_at < now() - stale_after_seconds``) is built
        **in-query** by :func:`~aleph.repositories._generation.stale_cutoff`
        (via ``claimable_predicate``) and evaluated on the **database clock**,
        not the app clock — Fly (the app) and Neon (the database) are different
        hosts, and comparing an app-clock ``datetime`` against a
        ``func.now()``-written column would let clock skew either re-claim a
        healthy in-flight run or never recover a crashed one.

        Returns the **fencing token** (the ``started_at`` this claim wrote) on a
        win, or ``None`` on a loss — the caller (the service) re-reads
        :meth:`get_draft_run` to decide the no-op ``202`` response's shape.
        Commit immediately after a win, exactly as
        ``LessonRepository.claim_for_generation`` requires: the row lock is held
        to commit, and a long-open claim both blocks competitors and freezes
        the stale clock.
        """
        result = await self.session.execute(
            pg_insert(FlashcardDraftRun)
            .values(
                lesson_id=lesson_id,
                state=FlashcardDraftRunState.GENERATING,
                started_at=func.now(),
                error=None,
            )
            .on_conflict_do_update(
                index_elements=[FlashcardDraftRun.lesson_id],
                set_={
                    "state": FlashcardDraftRunState.GENERATING,
                    "started_at": func.now(),
                    "error": None,
                    "updated_at": func.now(),
                },
                where=claimable_predicate(
                    state_col=FlashcardDraftRun.state,
                    started_at_col=FlashcardDraftRun.started_at,
                    claimable_states=(FlashcardDraftRunState.FAILED,),
                    generating_state=FlashcardDraftRunState.GENERATING,
                    stale_after_seconds=stale_after_seconds,
                ),
            )
            .returning(FlashcardDraftRun.started_at)
        )
        return result.scalar_one_or_none()

    async def get_draft_run(self, lesson_id: uuid.UUID) -> FlashcardDraftRun | None:
        """One lesson's draft run row, or ``None`` if drafting was never triggered.

        The **raw**, stored state — never collapsed for staleness. This is
        what the claim protocol's own tests assert against (a `generated` run
        reads as `generated` here no matter how old ``started_at`` is); the
        poll's stale-aware read is :meth:`get_effective_draft_run_state`.
        """
        return await self.session.get(FlashcardDraftRun, lesson_id)

    async def get_effective_draft_run_state(
        self, lesson_id: uuid.UUID
    ) -> FlashcardDraftRunState | None:
        """The lesson's draft run **effective** state — the poll's stale-aware read.

        A crashed worker (a Fly machine restart, a task cancelled at shutdown —
        neither caught by ``services/flashcard_drafting.py``'s own top-level
        ``except Exception``) leaves the row ``generating`` forever with no
        further ``POST`` ever coming. Left to :meth:`get_draft_run`'s raw
        value, the poll would report ``"generating"`` forever, the frontend's
        `DraftList` spinner would never resolve, and the retry affordance only
        exists on the `failed` branch (§5.6) — a permanent dead spinner with no
        recovery.

        Built through :func:`aleph.repositories._generation.effective_state_case`
        — the one CASE ``LessonRepository.effective_state`` /
        ``PathRepository`` already share — so a ``generating`` run older than
        ``stale_after_seconds`` reads as ``failed`` here, on the **database
        clock**, exactly like :meth:`claim_draft_run`'s own
        :func:`~aleph.repositories._generation.claimable_predicate`. That
        surfaces the existing retry affordance, which re-claims through
        ``claim_draft_run``'s ``WHERE state = 'failed'`` arm — the row was
        already re-claimable after the stale window; this is what makes the
        poll actually report it as such.

        ``None`` when drafting was never triggered (`flashcard_draft_runs` is
        sparse, D7) — a real, distinct case from every state member, mapped by
        the caller to ``"not_started"``.
        """
        result = await self.session.execute(
            select(self._effective_draft_run_state_expr()).where(
                FlashcardDraftRun.lesson_id == lesson_id
            )
        )
        value = result.scalar_one_or_none()
        return FlashcardDraftRunState(value) if value is not None else None

    async def mark_draft_run_generated(
        self, *, lesson_id: uuid.UUID, fence: datetime.datetime
    ) -> bool:
        """Resolve a claimed run to ``generated``. Fenced like ``LessonRepository``'s
        ``mark_generated``: writes only while still ``generating`` with this exact
        ``started_at``, so a stalled worker that lost its claim to a re-claim is a
        no-op. Returns whether *this* call performed the transition.
        """
        result = await self.session.execute(
            update(FlashcardDraftRun)
            .where(
                FlashcardDraftRun.lesson_id == lesson_id,
                FlashcardDraftRun.state == FlashcardDraftRunState.GENERATING,
                FlashcardDraftRun.started_at == fence,
            )
            .values(
                state=FlashcardDraftRunState.GENERATED,
                error=None,
                updated_at=func.now(),
            )
        )
        return affected_rows(result) > 0

    async def mark_draft_run_failed(
        self, *, lesson_id: uuid.UUID, error: str, fence: datetime.datetime
    ) -> bool:
        """Resolve a claimed run to ``failed`` (retryable). Same fencing as
        :meth:`mark_draft_run_generated`.
        """
        result = await self.session.execute(
            update(FlashcardDraftRun)
            .where(
                FlashcardDraftRun.lesson_id == lesson_id,
                FlashcardDraftRun.state == FlashcardDraftRunState.GENERATING,
                FlashcardDraftRun.started_at == fence,
            )
            .values(
                state=FlashcardDraftRunState.FAILED,
                error=error,
                updated_at=func.now(),
            )
        )
        return affected_rows(result) > 0

    # -- drafting: the cards themselves (§5.2) -------------------------------- #

    async def create_drafts(
        self,
        *,
        user_id: uuid.UUID,
        source_lesson_id: uuid.UUID,
        source_path_id: uuid.UUID | None,
        source_lesson_title: str,
        source_path_title: str,
        source_generated_at: datetime.datetime,
        cards: Sequence[tuple[str, str]],
    ) -> list[Flashcard]:
        """Insert the agent's ``cards`` as draft rows (``kept_at``/``rung``/``due_on``
        all ``NULL``) — §5.2 step 3. ``cards`` is a sequence of ``(front, back)``
        pairs; the four ``source_*`` columns are copied once here, identically
        onto every draft from this run (D12).
        """
        drafts = [
            Flashcard(
                user_id=user_id,
                front=front,
                back=back,
                source_lesson_id=source_lesson_id,
                source_path_id=source_path_id,
                source_lesson_title=source_lesson_title,
                source_path_title=source_path_title,
                source_generated_at=source_generated_at,
            )
            for front, back in cards
        ]
        self.session.add_all(drafts)
        await self.session.flush()
        return drafts

    async def list_drafts_for_lesson(self, lesson_id: uuid.UUID) -> list[Flashcard]:
        """A lesson's pending drafts (``kept_at IS NULL``), creation order.

        The poll endpoint's read (``GET .../flashcard-drafts``) and the keep
        screen's list — both want the same "what did the agent propose" set.
        """
        result = await self.session.execute(
            select(Flashcard)
            .where(
                Flashcard.source_lesson_id == lesson_id,
                Flashcard.kept_at.is_(None),
            )
            .order_by(Flashcard.created_at)
        )
        return list(result.scalars())

    async def keep_drafts(
        self, *, lesson_id: uuid.UUID, kept_ids: Sequence[uuid.UUID], due_on: date
    ) -> int:
        """The §5.2 keep transaction: keep ``kept_ids``, discard every other draft.

        Two statements, run against the caller's session/transaction so they
        land atomically together:

        1. ``UPDATE flashcards SET kept_at = now(), rung = 0, due_on = :due_on
           WHERE id IN :kept_ids AND source_lesson_id = :lesson_id AND kept_at
           IS NULL`` — scoped to *this lesson's* drafts, so an id belonging to
           another lesson's pending drafts cannot be kept here.
        2. ``DELETE FROM flashcards WHERE source_lesson_id = :lesson_id AND
           kept_at IS NULL`` — every draft this call did not just keep, gone
           for good (PRD §3: discarded drafts are not saved anywhere).

        ``due_on`` is **already computed** by the caller (``today +
        ladder[0]``, D1's projection at entry — this layer does not know the
        ladder). ``kept_ids = ()`` is "Skip — keep none": the update touches
        nothing and the delete removes every draft.

        ``kept_ids`` **must already be distinct** — the caller
        (``services/flashcard_drafting.py::_keep_drafts``) dedupes exactly
        once, order-preservingly, before calling this method; deduping again
        here would be a second place to be wrong about what "distinct" means
        for the same count contract. This method does not defend against a
        duplicate-laden input: an ``id.in_(...)`` with repeats still matches
        each physical row at most once (a row cannot be updated twice by one
        ``UPDATE``), so the write itself would be unaffected either way — but
        the **count** this returns is compared by the caller against
        ``len(kept_ids)`` to detect a foreign id, and that comparison is only
        meaningful when ``kept_ids`` carries no repetition to begin with.
        Returns the update's row count: the number of ``kept_ids`` that turned
        out to be drafts of this lesson.

        **The UPDATE runs before the unconditional DELETE, in the same
        transaction.** A caller enforcing §11's "a foreign draft id is a 404
        **and mutates nothing**" must check the returned count *before*
        committing and roll back (never commit) on a short count — the DELETE
        has already run by the time this method returns, so committing first
        and 404ing after would still have discarded this lesson's other
        pending drafts despite the request being rejected.
        """
        kept_count = 0
        if kept_ids:
            result = await self.session.execute(
                update(Flashcard)
                .where(
                    Flashcard.id.in_(kept_ids),
                    Flashcard.source_lesson_id == lesson_id,
                    Flashcard.kept_at.is_(None),
                )
                .values(
                    kept_at=func.now(),
                    rung=0,
                    due_on=due_on,
                    updated_at=func.now(),
                )
            )
            kept_count = affected_rows(result)
        await self.session.execute(
            delete(Flashcard).where(
                Flashcard.source_lesson_id == lesson_id,
                Flashcard.kept_at.is_(None),
            )
        )
        return kept_count

    # -- card management: list / edit / delete (AL-410 / issue #156, §2) ----- #

    async def list_cards_for_user(
        self,
        *,
        user_id: uuid.UUID,
        limit: int,
        cursor: str | None,
        path_id: uuid.UUID | None,
        query: str | None,
    ) -> CardPage:
        """The card list's browse read (§2): every **kept**, non-deleted card,
        most-recently-kept first, cursor-paginated.

        The base filter — ``user_id``, ``kept_at IS NOT NULL``, ``deleted_at
        IS NULL`` — and the ``Lesson`` left-join mirror :meth:`cards_by_ids`
        exactly, so the citation degrades identically here and on the queue
        (D12): one join shape, reused, never re-derived.

        ``path_id`` narrows to one path's cards (``source_path_id ==
        path_id``). ``query`` is a case-insensitive substring match on
        **either** ``front`` or ``back`` — the search bar looks at both sides
        of a card, not just its prompt — with ``%``/``_``/the escape
        character itself escaped by :func:`_escape_like` before reaching
        ``ILIKE``; an unescaped ``%`` in a learner's search text would
        otherwise be silently read as a wildcard and match every card.

        **Cursor, not offset** (§2) — stable across a concurrent delete, which
        an offset is not: deleting row 3 of a 20-row page shifts every row
        after it up by one, so an offset-based "page 2" would skip or repeat a
        row depending on which side of the delete it fell. ``cursor`` decodes
        via :func:`_parse_cursor` to ``(kept_at, card_id)`` and is applied as
        one row-comparison predicate — ``tuple_(kept_at, id) <
        tuple_(cursor_kept_at, cursor_id)`` — matching the ``ORDER BY kept_at
        DESC, id DESC`` below exactly, rather than a hand-expanded ``OR``
        that is easy to get subtly wrong at the tie-break. A malformed
        ``cursor`` propagates :class:`InvalidCursorError` to the caller
        (``services/reviews.py`` turns it into a ``422``).

        ``limit`` is capped at :data:`MAX_CARD_LIST_LIMIT` here too — defense
        in depth alongside the DTO/query-param cap (§5) — and fetched as
        ``capped_limit + 1``: when the extra row comes back, the page is the
        first ``capped_limit`` rows and ``next_cursor`` is built from the last
        *returned* row (:func:`_build_cursor`); otherwise there is no next
        page and ``next_cursor`` is ``None``.

        **Also floored at 1** (``max(1, min(limit, MAX_CARD_LIST_LIMIT))``),
        not just capped from above — the router's own ``Query(20, ge=1,
        le=50)`` never lets ``limit <= 0`` reach here, but this method's own
        docstring (and :data:`MAX_CARD_LIST_LIMIT`'s) sell the repo-level cap
        as protection "for a caller that reaches this repository directly,
        bypassing the router," and for that caller a bare ``min()`` is a trap:
        ``limit=0`` (or negative) would still fetch ``capped_limit + 1 == 1``
        row, the lookahead row makes ``has_more`` true, ``page_rows`` (the
        first ``0`` of those rows) is empty, and ``page_rows[-1]`` in the
        ``next_cursor`` branch below raises ``IndexError`` — a ``500`` for
        exactly the caller this cap claims to protect. Flooring at 1 makes the
        clamp actually total for any integer input, matching the docstring's
        claim instead of only defending the upper bound.
        """
        conditions = [
            Flashcard.user_id == user_id,
            Flashcard.kept_at.isnot(None),
            Flashcard.deleted_at.is_(None),
        ]
        if path_id is not None:
            conditions.append(Flashcard.source_path_id == path_id)
        if query:
            needle = f"%{_escape_like(query)}%"
            conditions.append(
                or_(
                    Flashcard.front.ilike(needle, escape="\\"),
                    Flashcard.back.ilike(needle, escape="\\"),
                )
            )
        if cursor is not None:
            cursor_kept_at, cursor_id = _parse_cursor(cursor)
            conditions.append(
                tuple_(Flashcard.kept_at, Flashcard.id)
                < tuple_(literal(cursor_kept_at), literal(cursor_id))
            )

        capped_limit = max(1, min(limit, MAX_CARD_LIST_LIMIT))
        result = await self.session.execute(
            select(
                Flashcard.id,
                Flashcard.front,
                Flashcard.back,
                Flashcard.rung,
                Flashcard.due_on,
                Flashcard.kept_at,
                Flashcard.edited_at,
                Flashcard.source_lesson_id,
                Flashcard.source_path_id,
                Flashcard.source_lesson_title,
                Flashcard.source_path_title,
                Flashcard.source_generated_at,
                Lesson.generated_at.label("current_lesson_generated_at"),
            )
            .select_from(Flashcard)
            .outerjoin(Lesson, Lesson.id == Flashcard.source_lesson_id)
            .where(*conditions)
            .order_by(Flashcard.kept_at.desc(), Flashcard.id.desc())
            .limit(capped_limit + 1)
        )
        rows = result.all()
        has_more = len(rows) > capped_limit
        page_rows = rows[:capped_limit]
        cards = [
            FlashcardRecord(
                id=row.id,
                front=row.front,
                back=row.back,
                rung=row.rung,
                due_on=row.due_on,
                kept_at=row.kept_at,
                edited_at=row.edited_at,
                source_lesson_id=row.source_lesson_id,
                source_path_id=row.source_path_id,
                source_lesson_title=row.source_lesson_title,
                source_path_title=row.source_path_title,
                source_generated_at=row.source_generated_at,
                current_lesson_generated_at=row.current_lesson_generated_at,
            )
            for row in page_rows
        ]
        next_cursor = (
            _build_cursor(page_rows[-1].kept_at, page_rows[-1].id) if has_more else None
        )
        return CardPage(cards=cards, next_cursor=next_cursor)

    async def update_card_text(
        self, *, user_id: uuid.UUID, card_id: uuid.UUID, front: str, back: str
    ) -> FlashcardRecord | None:
        """Rewrite a kept card's ``front``/``back`` (§3): ``UPDATE flashcards
        SET front, back, edited_at = now(), updated_at = now() WHERE id = :id
        AND user_id = :uid AND kept_at IS NOT NULL AND deleted_at IS NULL``.

        **Never touches ``rung``/``due_on``** — fixing a typo does not reset
        what the learner already knows; only a graded review moves those two
        columns (§5.4's own transaction owns them exclusively). Returns
        ``None`` for unowned / unknown / a draft (``kept_at IS NULL``) /
        already-deleted — the caller (``services/reviews.py``) turns that into
        a ``404``, never a ``403`` (§4 item 3's posture, held here too).

        Re-reads through :meth:`cards_by_ids` rather than a ``RETURNING``
        clause: the citation still needs the ``Lesson`` join (D12), which a
        plain ``UPDATE ... RETURNING`` cannot express, and a second query on
        exactly one id is cheap next to a hand-rolled join-returning
        statement.
        """
        result = await self.session.execute(
            update(Flashcard)
            .where(
                Flashcard.id == card_id,
                Flashcard.user_id == user_id,
                Flashcard.kept_at.isnot(None),
                Flashcard.deleted_at.is_(None),
            )
            .values(front=front, back=back, edited_at=func.now(), updated_at=func.now())
        )
        if affected_rows(result) == 0:
            return None
        records = await self.cards_by_ids(user_id=user_id, card_ids=[card_id])
        return records[0] if records else None

    async def soft_delete_card(self, *, user_id: uuid.UUID, card_id: uuid.UUID) -> bool:
        """Soft-delete a kept card (see ``Flashcard.deleted_at``'s docstring
        for why it is soft): ``UPDATE flashcards SET deleted_at = now(),
        updated_at = now() WHERE id = :id AND user_id = :uid AND kept_at IS
        NOT NULL AND deleted_at IS NULL``.

        ``False`` for unowned / unknown / a draft / **already-deleted** — the
        last of those is deliberate, not a gap: a double-tapped delete finds
        no row left matching its own ``deleted_at IS NULL`` predicate, so a
        second call is an honest ``404`` (via the caller) rather than a silent
        second success. The row and its ``flashcard_reviews`` are otherwise
        untouched by this statement — no cascade, no data loss.
        """
        result = await self.session.execute(
            update(Flashcard)
            .where(
                Flashcard.id == card_id,
                Flashcard.user_id == user_id,
                Flashcard.kept_at.isnot(None),
                Flashcard.deleted_at.is_(None),
            )
            .values(deleted_at=func.now(), updated_at=func.now())
        )
        return affected_rows(result) > 0
