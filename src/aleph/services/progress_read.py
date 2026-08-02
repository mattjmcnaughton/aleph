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
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from aleph.config import settings
from aleph.domains.streaks import activity_window, compute_streaks
from aleph.repositories import LessonRepository

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

    ``current_streak``/``best_streak``/``completed_today`` are the **global**
    Daily streak — the union of Active days across every path. ``activity`` is
    always exactly ``settings.streak_activity_window_days`` cells (§13),
    oldest first, ending at ``today``. ``paths`` omits any path with no
    completions (D5) and is sorted by ``path_id`` for a stable wire order —
    the query's own row order is not itself a contract worth exposing.
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
    now: datetime | None = None,
) -> ProgressSummaryView:
    """Compose the Progress API's whole payload for one learner (§5.3/§6).

    Production entry point: builds the real repository from ``session`` and
    delegates every actual decision to :func:`_summarize`, which is what the
    unit tests call directly against a fake.
    """
    return await _summarize(
        LessonRepository(session),
        user_id=user_id,
        tz_offset_minutes=tz_offset_minutes,
        now=now,
    )


async def _summarize(
    repository: CompletionDaysReader,
    *,
    user_id: uuid.UUID,
    tz_offset_minutes: int,
    now: datetime | None = None,
) -> ProgressSummaryView:
    """The service's real logic, seamed on a :class:`CompletionDaysReader`.

    Separated from :func:`load_progress_summary` purely for testability — see
    the module docstring's "fake-repository seam" section. Every semantic
    decision the TDD assigns to the service (owning "today", the two folds,
    ``completed_today``, path absence) happens here.
    """
    rows = await repository.completion_days_for_user(
        user_id=user_id, tz_offset_minutes=tz_offset_minutes
    )

    resolved_now = now if now is not None else datetime.now(UTC)
    today = (resolved_now - timedelta(minutes=tz_offset_minutes)).date()

    # One pass over the rows builds both folds at once: a per-day count summed
    # across every path (the activity strip and the global ``completed_today``
    # both want this) and a per-path grouping (each path's own streak).
    counts_by_day: dict[date, int] = defaultdict(int)
    rows_by_path: dict[uuid.UUID, list[CompletionDay]] = defaultdict(list)
    for row in rows:
        counts_by_day[row.day] += row.count
        rows_by_path[row.path_id].append(row)

    global_streaks = compute_streaks(set(counts_by_day), today=today)

    paths = [
        _path_streak_view(path_id, path_rows, today=today)
        for path_id, path_rows in sorted(
            rows_by_path.items(), key=lambda item: str(item[0])
        )
    ]

    return ProgressSummaryView(
        today=today,
        current_streak=global_streaks.current,
        best_streak=global_streaks.best,
        completed_today=counts_by_day.get(today, 0),
        activity=activity_window(
            counts_by_day, today=today, days=settings.streak_activity_window_days
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
