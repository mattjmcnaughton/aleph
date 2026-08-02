"""Unit tests for :mod:`aleph.domains.streaks` (TDD §5.1/§11 table).

Pure data in, pure data out — no DB, no fakes, no mocks. Every row of the TDD
§11 unit-test table gets its own test, named for the case it pins.
"""

from __future__ import annotations

from datetime import date, timedelta

from aleph.domains.streaks import (
    ActivityCell,
    Streaks,
    activity_window,
    compute_streaks,
)

_TODAY = date(2026, 8, 2)


def _days_ago(*offsets: int) -> set[date]:
    """A set of dates ``offsets`` days before :data:`_TODAY` (0 = today)."""
    return {_TODAY - timedelta(days=offset) for offset in offsets}


def _run(end_offset: int, length: int) -> set[date]:
    """A run of ``length`` consecutive active days ending ``end_offset`` days ago."""
    return _days_ago(*range(end_offset, end_offset + length))


# -- compute_streaks: the §11 table --------------------------------------- #


def test_empty_input_yields_zero_and_zero() -> None:
    assert compute_streaks(set(), today=_TODAY) == Streaks(current=0, best=0)


def test_single_active_day_equal_to_today() -> None:
    assert compute_streaks(_days_ago(0), today=_TODAY) == Streaks(current=1, best=1)


def test_run_of_five_ending_today() -> None:
    active = _run(0, 5)
    assert compute_streaks(active, today=_TODAY) == Streaks(current=5, best=5)


def test_run_of_five_ending_yesterday_today_empty() -> None:
    """PRD §4.4's grace day: the streak survives an empty *today*."""
    active = _run(1, 5)
    assert compute_streaks(active, today=_TODAY) == Streaks(current=5, best=5)


def test_today_and_yesterday_both_empty_best_survives_current_does_not() -> None:
    active = _run(5, 9)  # ends 5 days ago, so neither today nor yesterday is active
    assert compute_streaks(active, today=_TODAY) == Streaks(current=0, best=9)


def test_best_is_the_longest_run_not_the_latest() -> None:
    # An earlier 6-day run, then a later 2-day run touching today.
    earlier_long_run = _run(20, 6)
    later_short_run = _run(0, 2)
    active = earlier_long_run | later_short_run
    result = compute_streaks(active, today=_TODAY)
    assert result.current == 2
    assert result.best == 6


def test_a_single_gap_day_splits_one_run_into_two() -> None:
    # Days 0-2 active, day 3 a gap, days 4-6 active: two independent 3-day runs.
    active = _days_ago(0, 1, 2, 4, 5, 6)
    result = compute_streaks(active, today=_TODAY)
    assert result.current == 3  # the run touching today
    assert result.best == 3  # both runs tie; best is not inflated by the gap


def test_out_of_order_and_duplicate_input_is_structurally_impossible() -> None:
    """A set cannot carry duplicates or an order, by construction.

    Building the same days from two different orderings (and via a list with
    repeats, coerced to a set) must compute identically — there is no ordering
    step for the domain to get wrong because there is nothing to order.
    """
    forward = set(_run(0, 5))
    backward = set(reversed(sorted(_run(0, 5))))
    from_list_with_duplicates = set([*_run(0, 5), *_run(0, 5), _TODAY])

    assert compute_streaks(forward, today=_TODAY) == compute_streaks(
        backward, today=_TODAY
    )
    assert compute_streaks(forward, today=_TODAY) == compute_streaks(
        from_list_with_duplicates, today=_TODAY
    )


def test_best_is_always_at_least_current() -> None:
    """``best >= current`` for every case in this file, plus a few more shapes."""
    cases = [
        set(),
        _days_ago(0),
        _run(0, 5),
        _run(1, 5),
        _run(5, 9),
        _run(20, 6) | _run(0, 2),
        _days_ago(0, 1, 2, 4, 5, 6),
        _days_ago(3, 4, 100, 101, 102),
        _days_ago(-2, -1, 0),  # a "future" day (clock skew) extends the run
    ]
    for active in cases:
        result = compute_streaks(active, today=_TODAY)
        assert result.best >= result.current, active


def test_a_future_day_extends_the_best_run_rather_than_being_special_cased() -> None:
    """Recorded in TDD §5.1: no clamping to ``today``.

    ``current`` only ever walks *backward* from its anchor, so a day ahead of
    today cannot lengthen it — but ``best`` scans every run in the input, and a
    3-day run that happens to include tomorrow is still a 3-day run.
    """
    active = _days_ago(-1, 0, 1)  # tomorrow, today, and yesterday: one 3-day run
    result = compute_streaks(active, today=_TODAY)
    assert result.current == 2  # backward from today: today + yesterday
    assert result.best == 3  # the whole run, future day included


# -- activity_window -------------------------------------------------------- #


def test_activity_window_has_exactly_days_cells_oldest_first_ending_at_today() -> None:
    counts = {_TODAY: 2, _TODAY - timedelta(days=1): 1}
    window = activity_window(counts, today=_TODAY, days=7)

    assert len(window) == 7
    assert window[0].day == _TODAY - timedelta(days=6)
    assert window[-1].day == _TODAY
    assert [cell.day for cell in window] == sorted(cell.day for cell in window)


def test_activity_window_zero_fills_gaps() -> None:
    counts = {_TODAY: 3}
    window = activity_window(counts, today=_TODAY, days=3)

    assert window == [
        ActivityCell(day=_TODAY - timedelta(days=2), count=0),
        ActivityCell(day=_TODAY - timedelta(days=1), count=0),
        ActivityCell(day=_TODAY, count=3),
    ]


def test_activity_window_drops_days_outside_the_window() -> None:
    far_outside = _TODAY - timedelta(days=1000)
    counts = {far_outside: 99, _TODAY: 1}
    window = activity_window(counts, today=_TODAY, days=2)

    assert far_outside not in {cell.day for cell in window}
    assert sum(cell.count for cell in window) == 1
