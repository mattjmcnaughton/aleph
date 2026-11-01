"""Milestones count distinct learning days, not lessons or views."""

from datetime import date, timedelta

import pytest

from aleph.domains.milestones import Milestone, earned_milestones

TODAY = date(2026, 10, 31)


@pytest.mark.parametrize("threshold", [100, 500, 1000])
def test_same_completion_can_earn_both_metrics(threshold: int) -> None:
    before = {TODAY - timedelta(days=i) for i in range(1, threshold)}
    assert earned_milestones(
        before,
        today=TODAY,
    ) == [
        Milestone("daily_streak", threshold),
        Milestone("active_days", threshold),
    ]


def test_gap_only_breaks_consecutive_metric() -> None:
    before = {TODAY - timedelta(days=i * 2) for i in range(1, 100)}
    assert earned_milestones(
        before,
        today=TODAY,
    ) == [Milestone("active_days", 100)]


def test_second_completion_today_does_not_earn_anything() -> None:
    days = {TODAY - timedelta(days=i) for i in range(100)}
    assert earned_milestones(days, today=TODAY) == []


def test_rebuilt_streak_can_celebrate_again() -> None:
    previous_run = {TODAY - timedelta(days=i) for i in range(200, 300)}
    current_run = {TODAY - timedelta(days=i) for i in range(1, 100)}
    before = previous_run | current_run
    assert earned_milestones(
        before,
        today=TODAY,
    ) == [Milestone("daily_streak", 100)]


@pytest.mark.parametrize("count", [1, 99, 101, 499, 501, 999, 1001])
def test_only_exact_thresholds(count: int) -> None:
    before = {TODAY - timedelta(days=i) for i in range(1, count)}
    assert (
        earned_milestones(
            before,
            today=TODAY,
        )
        == []
    )
