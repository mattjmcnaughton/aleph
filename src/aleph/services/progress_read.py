"""Read-side composition for the Progress API (Phase 5 TDD §5.3).

The ``paths_read`` shape (module-level frozen views + one async function
taking ``session`` first), applied to the Streaks slice: one repository read
(``LessonRepository.completion_days_for_user``, §5.2) folded two ways — once
across every path for the global **Daily streak**, once per path for the
**Path streak** breakdown — plus the activity strip, all composed here so
``routers/v1/progress.py`` stays parse/authz/translate (layering: routers ->
services -> repositories/domains).

**The service is the sole owner of "today."** The repository takes an offset
and returns already-local-day rows; the pure domain
(:mod:`aleph.domains.streaks`) takes a set of dates and a ``today``; neither of
them derives "today" itself. This module is the one place that turns "now,
plus an offset" into a date — via ``now`` (a keyword-only, defaulted test seam:
``now or datetime.now(UTC)``) and ``tz_offset_minutes``, exactly the arithmetic
TDD D3 specifies (``(now - timedelta(minutes=tz_offset_minutes)).date()``). No
production caller ever passes ``now``; a fixed unit-test ``datetime`` is what
lets the two-hemisphere sign-convention test (§11, "the test this feature most
needs") run with no clock to freeze and no import to patch.

**The fold happens twice over one list of rows.** Global: the union of every
row's day, regardless of path. Per path: group rows by ``path_id``, run
:func:`~aleph.domains.streaks.compute_streaks` on each group independently —
two paths worked on the same day fold to *one* global Active day, but each
path's own streak is unaffected by what happened on the other. The activity
strip sums counts **across paths** per day (D5: the heatmap is global), which
is why it is built from the raw rows rather than from either fold.

**Paths with no completions are absent** (D5, §14 R2) — this service does not
read the path list to zero-fill a path the query has no row for, which is the
second database round trip D4 exists to avoid entirely.

**The fake-repository seam.** :func:`load_progress_summary` is the production
entry point and always builds a real :class:`~aleph.repositories.LessonRepository`
from its ``session``, matching the ``paths_read`` public signature exactly
(``session`` first, no repository parameter to thread through every caller).
The actual folding logic lives in :func:`_summarize`, which takes a
:class:`CompletionDaysReader` — a `Protocol` a real repository satisfies
structurally and a unit test satisfies with a few lines of in-memory dict
(CLAUDE.md: fakes over mocks). Splitting the two is what makes the service's
own logic — the two folds, "today", ``completed_today`` — testable with zero
database, while the public function callers actually import stays a one-line
call.

**Phase 3 widens the seam (TDD D11/§5.5): a second reader, unioned into the
global fold only.** :class:`ReviewDaysReader` is the ``FlashcardRepository``
capability this service needs — kept to the one method the service calls, the
same discipline as :class:`CompletionDaysReader`. ``load_progress_summary``
takes a plain, **required, keyword-only** ``flashcards_enabled: bool`` rather
than importing ``services.feature_flags`` itself: the flag is resolved once,
in the router (``routers/v1/progress.py``, which already resolves ``streaks``
the same way), and handed down as a boolean so this module stays decoupled
from flag resolution and keeps its existing fake-repository testability.
Deliberately **no default** — a defaulted ``False`` here is a forgotten caller
away from silently shipping the union off with no test failing to say so
(exactly what happened once already: see the call sites this module's own
test suite had to make explicit). When the flag is off, no
:class:`FlashcardRepository` is even constructed — ``review_days_for_user`` is
provably never called, which is what makes TDD D10's kill switch honest: with
``flashcards`` off, the streak is bit-identical to Phase 5's own output.

**The union lands in exactly one place.** ``global_streaks`` folds
``completion_days | review_days`` (D11) — the **per-path** fold
(``rows_by_path`` / ``_path_streak_view``) never sees a review, because a
flashcard belongs to the learner, not a path (PRD §4.9, CONTEXT.md's **Path
streak** row). ``completed_today`` also never sees a review: it is rendered by
the frontend as "N lessons today", and a review is not a lesson completion, so
it stays ``counts_by_day.get(today, 0)`` over lesson rows alone. The activity
strip is the one exception in the *other* direction — see
:func:`_summarize`'s inline comment for why a review-only day still has to
paint a non-empty cell.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from aleph.config import settings
from aleph.domains.streaks import activity_window, compute_streaks
from aleph.repositories import FlashcardRepository, LessonRepository

if TYPE_CHECKING:
    import uuid
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.domains.streaks import ActivityCell
    from aleph.repositories import CompletionDay


class CompletionDaysReader(Protocol):
    """The one repository capability this service needs.

    ``LessonRepository`` satisfies this structurally (no inheritance needed);
    a unit test substitutes a small in-memory fake instead. Kept to exactly the
    one method the service calls — a narrower seam is a smaller thing for a
    fake to have to get right.
    """

    async def completion_days_for_user(
        self, *, user_id: uuid.UUID, tz_offset_minutes: int
    ) -> list[CompletionDay]: ...


class ReviewDaysReader(Protocol):
    """The one ``FlashcardRepository`` capability the streak union needs (D11).

    ``FlashcardRepository`` satisfies this structurally; a unit test
    substitutes a few lines of in-memory fake, the same shape as
    :class:`CompletionDaysReader`'s. Kept to exactly the one method the
    service calls, for the same reason that Protocol is.
    """

    async def review_days_for_user(
        self, *, user_id: uuid.UUID, tz_offset_minutes: int
    ) -> list[date]: ...


@dataclass(frozen=True)
class PathStreakView:
    """One path's streak (CONTEXT.md: **Path streak**), independent of the rest."""

    path_id: uuid.UUID
    current_streak: int
    best_streak: int
    completed_today: int


@dataclass(frozen=True)
class ProgressSummaryView:
    """The composed snapshot ``routers/v1/progress.py`` translates to the DTO.

    ``current_streak``/``best_streak`` are the **global** Daily streak — the
    union of Active days across every path, **and, when the ``flashcards`` flag
    is on, every review day too** (TDD D11/§5.5). ``completed_today`` stays
    lesson completions only — it is the frontend's "N lessons today", not an
    Active-day count, so a review never moves it. ``activity`` is always
    exactly ``settings.streak_activity_window_days`` cells (§13), oldest first,
    ending at ``today``; a review-only day still renders a non-empty cell (see
    ``_summarize``) so the strip cannot contradict the streak beside it.
    ``paths`` — the **Path streak** breakdown — omits any path with no
    completions (D5), never counts a review (PRD §4.9), and is sorted by
    ``path_id`` for a stable wire order — the query's own row order is not
    itself a contract worth exposing.
    """

    today: date
    current_streak: int
    best_streak: int
    completed_today: int
    activity: list[ActivityCell]
    paths: list[PathStreakView]


async def load_progress_summary(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tz_offset_minutes: int,
    flashcards_enabled: bool,
    now: datetime | None = None,
) -> ProgressSummaryView:
    """Compose the Progress API's whole payload for one learner (§5.3/§6).

    Production entry point: builds the real repository from ``session`` and
    delegates every actual decision to :func:`_summarize`, which is what the
    unit tests call directly against fakes.

    ``flashcards_enabled`` is the caller-resolved ``flashcards`` flag decision
    (D10) — the router resolves it once via ``FeatureFlagService``, the same
    way it already resolves ``streaks``, and hands down a plain boolean rather
    than this module importing the flag service itself (module docstring).
    **Required and keyword-only, with no default**: a forgotten caller must
    fail loudly (``TypeError``) rather than silently fold the streak union off
    with every existing test still green — the trap a defaulted ``False``
    quietly is. A :class:`~aleph.repositories.FlashcardRepository` is
    constructed **only** when the flag is on; when it is off, ``_summarize``
    receives no reader at all, so ``review_days_for_user`` is never called —
    the kill switch is honest by construction, not by a branch inside the
    query.
    """
    reviews = FlashcardRepository(session) if flashcards_enabled else None
    return await _summarize(
        LessonRepository(session),
        reviews,
        user_id=user_id,
        tz_offset_minutes=tz_offset_minutes,
        now=now,
    )


async def _summarize(
    repository: CompletionDaysReader,
    reviews: ReviewDaysReader | None = None,
    *,
    user_id: uuid.UUID,
    tz_offset_minutes: int,
    now: datetime | None = None,
) -> ProgressSummaryView:
    """The service's real logic, seamed on a :class:`CompletionDaysReader`
    and an optional :class:`ReviewDaysReader` (D11).

    Separated from :func:`load_progress_summary` purely for testability — see
    the module docstring's "fake-repository seam" section. Every semantic
    decision the TDD assigns to the service (owning "today", the two folds,
    ``completed_today``, path absence, the streak union) happens here.

    ``reviews`` defaults to ``None`` (flag off, or no second signal to fold
    in) — every existing call site that only knows about lesson completions
    keeps working unchanged.
    """
    rows = await repository.completion_days_for_user(
        user_id=user_id, tz_offset_minutes=tz_offset_minutes
    )
    review_days: set[date] = set()
    if reviews is not None:
        review_days = set(
            await reviews.review_days_for_user(
                user_id=user_id, tz_offset_minutes=tz_offset_minutes
            )
        )

    resolved_now = now if now is not None else datetime.now(UTC)
    today = (resolved_now - timedelta(minutes=tz_offset_minutes)).date()

    # One pass over the rows builds both folds at once: a per-day count summed
    # across every path (the activity strip and the global ``completed_today``
    # both want this) and a per-path grouping (each path's own streak).
    # ``counts_by_day`` is lesson completions only — reviews never enter it —
    # because it is also the source of ``completed_today`` and the per-path
    # fold, and neither may see a review (D11: "completed_today stays lesson
    # completions only"; PRD §4.9: reviews never count toward the Path streak).
    counts_by_day: dict[date, int] = defaultdict(int)
    rows_by_path: dict[uuid.UUID, list[CompletionDay]] = defaultdict(list)
    for row in rows:
        counts_by_day[row.day] += row.count
        rows_by_path[row.path_id].append(row)

    # The streak union (D11/§5.5): the *global* fold takes lesson-completion
    # days unioned with review days. The per-path fold below is built from
    # ``rows_by_path`` alone and never sees ``review_days`` — a flashcard
    # belongs to the learner, not a path (PRD §4.9).
    global_streaks = compute_streaks(set(counts_by_day) | review_days, today=today)

    paths = [
        _path_streak_view(path_id, path_rows, today=today)
        for path_id, path_rows in sorted(
            rows_by_path.items(), key=lambda item: str(item[0])
        )
    ]

    # The activity strip must not contradict the streak it sits beside: a day
    # that is Active in the global fold (because of a review alone) has to
    # render as a non-empty cell, or the strip would show a gap on a day the
    # streak counts. Reviews are days with no count (``review_days_for_user``
    # returns distinct days, not per-day tallies), so folding one in can only
    # ever *raise* a day to "at least one unit of activity" — never invent a
    # count higher than what a real tally would show, and never touch
    # ``counts_by_day`` itself (``completed_today`` and the per-path fold read
    # that dict directly, above, before this copy is made).
    activity_counts = dict(counts_by_day)
    for day in review_days:
        activity_counts[day] = max(activity_counts.get(day, 0), 1)

    return ProgressSummaryView(
        today=today,
        current_streak=global_streaks.current,
        best_streak=global_streaks.best,
        completed_today=counts_by_day.get(today, 0),
        activity=activity_window(
            activity_counts, today=today, days=settings.streak_activity_window_days
        ),
        paths=paths,
    )


def _path_streak_view(
    path_id: uuid.UUID, rows: list[CompletionDay], *, today: date
) -> PathStreakView:
    """One path's streak view from its own completion-day rows only."""
    streaks = compute_streaks({row.day for row in rows}, today=today)
    completed_today = next((row.count for row in rows if row.day == today), 0)
    return PathStreakView(
        path_id=path_id,
        current_streak=streaks.current,
        best_streak=streaks.best,
        completed_today=completed_today,
    )
