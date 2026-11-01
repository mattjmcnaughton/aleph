"""Exact learning-day milestones; no badge catalogue or retrospective awards."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from aleph.domains.streaks import compute_streaks

if TYPE_CHECKING:
    from collections.abc import Set
    from datetime import date

MilestoneMetric = Literal["daily_streak", "active_days"]
THRESHOLDS = (100, 500, 1000)


@dataclass(frozen=True)
class Milestone:
    metric: MilestoneMetric
    days: int


def earned_milestones(before: Set[date], *, today: date) -> list[Milestone]:
    """Recognize the first completion of this local day from retained history."""
    if today in before:
        return []
    # ponytail: derived history, not once-ever awards; rebuild/recross may repeat.
    after = before | {today}
    current = compute_streaks(after, today=today).current
    earned = []
    if current in THRESHOLDS:
        earned.append(Milestone("daily_streak", current))
    if len(after) in THRESHOLDS:
        earned.append(Milestone("active_days", len(after)))
    return earned
