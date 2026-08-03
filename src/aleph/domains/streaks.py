"""Pure streak derivation: current/best run length, and the activity strip (D1/D2).

**D1's whole payoff lives here.** A streak is never stored — it is a pure
function of the set of **Active days** (CONTEXT.md), which is itself derived
from ``lessons.completed_at`` by the repository query (§5.2). This module never
sees a row, a session, or a timezone: by the time a set of dates reaches
:func:`compute_streaks`, "what day is this" has already been decided, once, by
``services/progress_read.py`` (§5.3) — the domain only ever sees the answer.

That indirection is about to pay again. **Phase 3 widens Active day** to "a day
the learner completed a lesson *or* reviewed a flashcard" (Phase 3 PRD §4.9),
and nothing here changes: this function takes the set, not its provenance, so
the second signal is a union assembled one layer up. The *global* streak gains
it; the per-path streak does not (a card belongs to the learner, not a path).

**A port of habagou's ``compute_streaks``**, with the daily target collapsed to
1 (PRD §4.5): habagou's version takes a ``Mapping[date, int]`` and compares each
day's count to a configurable target; here the target *is* set membership, so
the input narrows to a plain ``Set[date]`` and there is no threshold to drift
(D2). :func:`compute_streaks` and :func:`activity_window` deliberately never
share an argument — the streak function does not need counts (a day either
happened or it didn't) and the window function does (three heatmap
intensities), so giving them the same signature would be the one hint that a
threshold might sneak back in.

Stdlib only, frozen inputs, no ORM — the ``domains/__init__.py`` contract
verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Set
    from datetime import date


@dataclass(frozen=True)
class Streaks:
    """A streak pair: the run ending "now" and the longest run ever.

    ``best >= current`` always (asserted by the unit tests rather than stated
    only here): the current run, when non-zero, is itself a run, so it can
    never exceed the longest one.
    """

    current: int
    best: int


@dataclass(frozen=True)
class ActivityCell:
    """One day of the activity strip: the calendar date and its completion count."""

    day: date
    count: int


def compute_streaks(active_days: Set[date], *, today: date) -> Streaks:
    """The current and best streak over a set of **Active days** (PRD §4.4/§4.5).

    ``current`` is the length of the run of consecutive days ending at
    ``today`` — **or at ``today - 1`` if today has no completion yet.** The
    streak does not break at midnight; it breaks only once a whole day has
    passed with nothing completed (PRD §4.4, "the grace day"). If neither
    ``today`` nor ``today - 1`` is active, ``current`` is ``0``.

    ``best`` is the longest run **anywhere** in the input, including runs that
    are not the latest one — a learner's best week last month still counts,
    even though their streak has since reset.

    ``active_days`` is a set, so duplicate or out-of-order input is
    structurally impossible to get wrong — there is no "sort it first" step to
    forget. Days in the future (clock skew, a travelling learner) are not
    special-cased: a future day simply extends whatever run it is part of. That
    is a recorded choice, not an oversight (TDD §5.1) — clamping to ``today``
    would make a learner who crosses the date line briefly *lose* a day they
    earned, which is worse than briefly gaining one.
    """
    return Streaks(
        current=_current_run_length(active_days, today=today),
        best=_longest_run_length(active_days),
    )


def _current_run_length(active_days: Set[date], *, today: date) -> int:
    """The run ending at ``today``, or at ``today - 1`` if ``today`` is empty."""
    anchor = today if today in active_days else today - timedelta(days=1)
    if anchor not in active_days:
        return 0
    return _run_length_ending_at(active_days, anchor)


def _run_length_ending_at(active_days: Set[date], anchor: date) -> int:
    """Count backwards from ``anchor`` (itself active) while each day is active."""
    length = 0
    day = anchor
    while day in active_days:
        length += 1
        day -= timedelta(days=1)
    return length


def _longest_run_length(active_days: Set[date]) -> int:
    """The longest run anywhere in ``active_days``, scanning each run once.

    A day only starts a scan if the day before it is *not* active — otherwise
    it is the middle or tail of a run some earlier day already measured. Every
    active day therefore belongs to exactly one scan, so this is O(n) in the
    number of active days rather than O(n^2). Each scan counts forward from
    the confirmed start (a single pass over the run), rather than walking
    forward to find the end and then back over the same days to count them.
    """
    best = 0
    for day in active_days:
        if day - timedelta(days=1) in active_days:
            continue
        length = 0
        run_day = day
        while run_day in active_days:
            length += 1
            run_day += timedelta(days=1)
        best = max(best, length)
    return best


def activity_window(
    counts: Mapping[date, int], *, today: date, days: int
) -> list[ActivityCell]:
    """Exactly ``days`` cells, oldest first, ending at ``today``.

    Every calendar day in ``[today - days + 1, today]`` gets a cell, zero-filled
    when ``counts`` has no entry for it; a day in ``counts`` that falls outside
    that span (impossible in practice — the caller only ever asks for the same
    window it counted — but not assumed here) is simply never visited. This is
    the heatmap's data (D12): three teal intensities need counts, which is the
    one thing :func:`compute_streaks` deliberately does not take.
    """
    return [
        ActivityCell(day=day, count=counts.get(day, 0))
        for day in (
            today - timedelta(days=offset) for offset in range(days - 1, -1, -1)
        )
    ]
