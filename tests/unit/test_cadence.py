"""Unit tests for the pure Cadence derivation (TDD D4/§5.1, §11's table).

Pure domain — no fakes, no I/O, no session. Every case in TDD §11's cadence
table is covered here, including the full 49-combination weekday sweep that
is where an off-by-one in "strictly after" would actually show up.
"""

from __future__ import annotations

import itertools
from datetime import date, timedelta

import pytest

from aleph.domains.cadence import is_claimable, next_claimable_on

# A known Monday, so `_MONDAY + timedelta(days=n)` walks Mon..Sun for n in 0..6.
_MONDAY = date(2026, 8, 3)

assert _MONDAY.weekday() == 0  # Monday, per Python's date.weekday() convention.


def _date_for_weekday(weekday: int) -> date:
    """A concrete date whose ``.weekday()`` is ``weekday``, in ``_MONDAY``'s week."""
    return _MONDAY + timedelta(days=weekday)


# --- no entries: claimable immediately, on any day (PRD §3) -------------------


@pytest.mark.parametrize("anchor_weekday", range(7))
@pytest.mark.parametrize(
    "today", [_MONDAY, _MONDAY + timedelta(days=3), _MONDAY + timedelta(days=200)]
)
def test_no_entries_is_claimable_on_any_day(today: date, anchor_weekday: int) -> None:
    assert is_claimable(None, anchor_weekday, today=today) is True


def test_no_entries_next_claimable_on_is_none() -> None:
    """None in, None out — is_claimable, not this function, turns it into True."""
    assert next_claimable_on(None, anchor_weekday=3) is None


# --- last entry yesterday, anchor is today: claimable --------------------------


def test_last_entry_yesterday_anchor_today_is_claimable() -> None:
    today = _date_for_weekday(4)
    last_entry_on = today - timedelta(days=1)

    assert is_claimable(last_entry_on, anchor_weekday=today.weekday(), today=today) is (
        True
    )


# --- last entry today, anchor is today: NOT claimable (strictly after) --------


def test_last_entry_today_anchor_today_is_not_claimable() -> None:
    """The floor is strictly after the last entry — a same-day re-run is refused."""
    today = _date_for_weekday(2)

    assert is_claimable(today, anchor_weekday=today.weekday(), today=today) is False


def test_last_entry_today_next_claimable_on_is_a_full_week_later() -> None:
    today = _date_for_weekday(5)

    assert next_claimable_on(today, anchor_weekday=today.weekday()) == (
        today + timedelta(days=7)
    )


# --- W32: a long absence produces one Brief, not a backlog --------------------


def test_six_weeks_absent_is_claimable_once_not_six_times() -> None:
    """A Beat idle six weeks satisfies the predicate once; there is no catch-up loop."""
    today = _date_for_weekday(1)
    last_entry_on = today - timedelta(weeks=6)
    anchor_weekday = today.weekday()

    assert is_claimable(last_entry_on, anchor_weekday, today=today) is True

    # next_claimable_on always answers "the *first* Anchor day after
    # last_entry_on" — at most 7 days later, never a run of six weekly dates
    # to work through. That single answer is what makes "claimable once" true
    # by construction rather than by a loop bound this test has to trust.
    next_date = next_claimable_on(last_entry_on, anchor_weekday)
    assert next_date is not None
    assert last_entry_on < next_date <= last_entry_on + timedelta(days=7)

    # Simulate the one run an arrival performs: the caller advances
    # last_entry_on to today (the new Brief/Skipped row's published_on). The
    # Beat is immediately not claimable again for the rest of today — no
    # second, third, … run fires for the same absence.
    assert is_claimable(today, anchor_weekday, today=today) is False


# --- a Skipped entry resets the floor exactly as a published one does ---------


def test_skipped_entry_resets_the_floor_like_a_published_one() -> None:
    """Neither function takes a "kind" — the caller's MAX(published_on) over
    both Skipped and published rows (D2) is what erases the distinction, and
    that means this module cannot special-case it even if it wanted to. The
    same date, however it was produced, yields the same floor.

    Asserted against the **concrete** expected date, not against itself:
    `f(x) == f(x)` cannot fail for any implementation (replacing
    `next_claimable_on`'s entire body with `return last_entry_on` would still
    pass a same-input-same-output comparison). The point this test has to
    make — that the function never learns which *kind* of entry it was —
    only shows up once both a published-row floor and a skipped-row floor are
    each checked against the real answer independently.

    2026-07-20 is a Monday; with ``anchor_weekday=4`` (Friday) the next Anchor
    day is 2026-07-24, so that date is claimable and the day before is not.
    """
    anchor_weekday = 4  # Friday
    next_anchor = date(2026, 7, 24)

    published_last_entry_on = date(2026, 7, 20)
    assert next_claimable_on(published_last_entry_on, anchor_weekday) == next_anchor
    assert (
        is_claimable(published_last_entry_on, anchor_weekday, today=next_anchor) is True
    )
    assert (
        is_claimable(
            published_last_entry_on,
            anchor_weekday,
            today=next_anchor - timedelta(days=1),
        )
        is False
    )

    skipped_last_entry_on = date(2026, 7, 20)  # same date, a Skipped row's floor
    assert next_claimable_on(skipped_last_entry_on, anchor_weekday) == next_anchor
    assert (
        is_claimable(skipped_last_entry_on, anchor_weekday, today=next_anchor) is True
    )
    assert (
        is_claimable(
            skipped_last_entry_on,
            anchor_weekday,
            today=next_anchor - timedelta(days=1),
        )
        is False
    )


# --- clock skew: today before the last entry (westward travel) ----------------


def test_today_before_last_entry_is_not_claimable() -> None:
    """Clock skew / westward travel: ``today`` lands before ``last_entry_on``.

    Current behavior returns ``False`` — pinned here, not changed. See
    ``cadence.py``'s comment on :func:`is_claimable` for why this is
    deliberate and self-healing rather than a bug to fix.
    """
    last_entry_on = date(2026, 8, 3)
    today = last_entry_on - timedelta(days=2)

    assert is_claimable(last_entry_on, anchor_weekday=4, today=today) is False


# --- leap day and year-boundary cases -------------------------------------


def test_leap_day_last_entry() -> None:
    """2024-02-29 is a Thursday; anchor Thursday -> 2024-03-07 (crosses the
    leap day itself, not just a month boundary)."""
    last_entry_on = date(2024, 2, 29)
    assert last_entry_on.weekday() == 3  # Thursday

    assert next_claimable_on(last_entry_on, anchor_weekday=3) == date(2024, 3, 7)


def test_year_boundary_last_entry() -> None:
    """2025-12-31 is a Wednesday; anchor Tuesday -> 2026-01-06, crossing the
    year boundary."""
    last_entry_on = date(2025, 12, 31)
    assert last_entry_on.weekday() == 2  # Wednesday

    assert next_claimable_on(last_entry_on, anchor_weekday=1) == date(2026, 1, 6)


# --- all 49 anchor-weekday x today-weekday combinations ------------------------

# Every (anchor_weekday, today_weekday) pair, Monday=0..Sunday=6 both axes.
# itertools.product over two 7-element ranges is inherently 49 distinct pairs;
# asserted explicitly anyway so a future edit that narrows either range fails
# loudly here rather than silently shrinking the sweep.
_WEEKDAY_PAIRS = list(itertools.product(range(7), range(7)))
assert len(_WEEKDAY_PAIRS) == 49


@pytest.mark.parametrize(
    ("anchor_weekday", "today_weekday"),
    _WEEKDAY_PAIRS,
    ids=[f"anchor={a}-today={t}" for a, t in _WEEKDAY_PAIRS],
)
def test_every_anchor_and_today_weekday_combination(
    anchor_weekday: int, today_weekday: int
) -> None:
    """The "last entry yesterday" case (above), generalized to every weekday.

    ``last_entry_on`` is fixed at exactly one day before ``today`` for every
    one of the 49 pairs. Claimable is then true **iff the anchor day is today
    itself** — the only way one day's gap can already reach the *next* Anchor
    day. Any off-by-one in the "strictly after" arithmetic (e.g. forgetting
    the Sunday(6) -> Monday(0) wraparound, or using ``> 0`` instead of
    ``== 0`` for the "forced to a full week" case) flips exactly one of these
    49 assertions.
    """
    today = _date_for_weekday(today_weekday)
    last_entry_on = today - timedelta(days=1)

    expected = anchor_weekday == today_weekday
    assert is_claimable(last_entry_on, anchor_weekday, today=today) is expected


def test_49_weekday_combinations_are_all_distinct() -> None:
    """Guards the sweep itself: 49 genuinely distinct pairs, not 7 repeated."""
    assert len(set(_WEEKDAY_PAIRS)) == 49
