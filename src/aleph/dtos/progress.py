"""Progress API DTOs: the streak summary (Phase 5 TDD §6).

The wire contract for ``GET /api/v1/progress/summary`` — the global **Daily
streak**, the activity strip, and the per-path **Path streak** breakdown, all in
one payload (D4: one endpoint, folding three concerns rather than three round
trips). DTOs are always separate from the ORM models and from
``services/progress_read.py``'s frozen views (CLAUDE.md); mapping between them
is explicit construction in ``routers/v1/progress.py`` — no ``from_attributes``,
following ``_progress_dto`` in ``routers/v1/paths.py``.
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field

# The client's ``getTimezoneOffset()`` value, verbatim (D3): minutes to
# *subtract* from UTC to reach the learner's local time — so a zone ahead of
# UTC (e.g. UTC+2) sends a **negative** number. ``±900`` is 15 hours, wider than
# any real UTC offset (UTC-12 .. UTC+14) but narrow enough that a garbage value
# cannot shift a day boundary arbitrarily far. A violation is a ``422`` through
# the shared validation envelope, never silently clamped. The sign convention
# has exactly one place to be wrong on the frontend (the options factory that
# calls ``getTimezoneOffset()``) and one place to be wrong here (this bound) —
# everything else just carries the number.
TzOffsetMinutes = Annotated[int, Field(ge=-900, le=900)]


class ActivityCellDTO(BaseModel):
    """One day of the 45-day activity strip (D12): a date and its lesson count.

    ``count`` is zero-filled for a day with no completions — the strip always
    carries exactly ``STREAK_ACTIVITY_WINDOW_DAYS`` entries, oldest first,
    ending at ``today`` (``domains.streaks.activity_window``).
    """

    date: date
    count: int


class PathStreakDTO(BaseModel):
    """One path's row in the per-path breakdown (CONTEXT.md: **Path streak**).

    A quieter stat than the global streak — the frontend hides its chip below 2
    days (PRD §4.3) and never celebrates it, but the payload carries the same
    three numbers as the global streak for consistency. A path with no
    completions is **absent** from the response's ``paths`` list entirely (D5,
    §14 R2) rather than appearing here with zeros.
    """

    path_id: UUID
    current_streak: int
    best_streak: int
    completed_today: int


class ProgressSummaryResponse(BaseModel):
    """``GET /api/v1/progress/summary`` body (§6): the whole streak payload.

    ``today`` is the learner's local calendar day, as the server resolved it
    from ``tz_offset_minutes`` — the service is the sole owner of "today"
    (§5.3), and echoing it back is what lets the frontend render "Day N" copy
    without recomputing the boundary itself.

    ``current_streak``/``best_streak``/``completed_today`` are the **global**
    Daily streak (CONTEXT.md) — the one that gets the flame. ``activity`` is
    always exactly ``STREAK_ACTIVITY_WINDOW_DAYS`` entries (§13); ``paths`` is a
    **list**, not a map keyed by path id (§6) — the frontend indexes it by
    ``path_id`` on arrival, and a list reads far better in a response body than
    a JSON object keyed by UUID.
    """

    today: date
    current_streak: int
    best_streak: int
    completed_today: int
    activity: list[ActivityCellDTO]
    paths: list[PathStreakDTO]
