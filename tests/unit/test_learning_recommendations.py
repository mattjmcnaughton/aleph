"""Pure Learning recommendation decisions and exact-topic identity."""

import pytest

from aleph.domains.learning_recommendations import (
    RecommendationConflict,
    normalize_topic,
    transition,
)
from aleph.services.feature_flags import ADMIN_DEFAULT_FLAGS, FLAG_DEFAULTS, FeatureFlag


def test_learning_tutor_is_dark_even_for_admins():
    assert FLAG_DEFAULTS[FeatureFlag.LEARNING_TUTOR] is False
    assert FeatureFlag.LEARNING_TUTOR not in ADMIN_DEFAULT_FLAGS


def test_exact_topic_normalizes_unicode_case_and_whitespace():
    assert normalize_topic("  ＳＴＲＡＳＳＥ\t\n Straße ") == "strasse strasse"


def test_decisions_and_capacity():
    assert transition("active", "dismiss", stale=False, active_count=3) == "dismissed"
    assert transition("dismissed", "restore", stale=False, active_count=2) == "active"
    with pytest.raises(RecommendationConflict, match="recommendation_capacity"):
        transition("dismissed", "restore", stale=False, active_count=3)
    for status in ("accepted", "superseded"):
        with pytest.raises(RecommendationConflict, match="recommendation_unavailable"):
            transition(status, "restore", stale=False, active_count=0)
    with pytest.raises(RecommendationConflict, match="recommendation_unavailable"):
        transition("dismissed", "restore", stale=True, active_count=0)
