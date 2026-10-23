"""Pure Learning recommendation identity, evidence and decision rules."""

import unicodedata
from typing import Literal

Kind = Literal["path", "beat"]
RecommendationStatus = Literal["active", "dismissed", "superseded", "accepted"]
Decision = Literal["dismiss", "restore"]
MAX_ACTIVE = 3


class RecommendationConflict(ValueError):  # noqa: N818 - domain outcome
    """A stable domain rejection reason, translated at the HTTP boundary."""


def normalize_topic(topic: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", topic).casefold().split())


def transition(
    status: str, decision: Decision, *, stale: bool, active_count: int
) -> RecommendationStatus:
    if stale or (status, decision) not in {
        ("active", "dismiss"),
        ("dismissed", "restore"),
    }:
        raise RecommendationConflict("recommendation_unavailable")
    if decision == "restore" and active_count >= MAX_ACTIVE:
        raise RecommendationConflict("recommendation_capacity")
    return "dismissed" if decision == "dismiss" else "active"
