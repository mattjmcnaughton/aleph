"""Removal contract for the retired flashcard and learner-settings surfaces."""

from aleph.app import create_app
from aleph.dtos.auth import UserDTO
from aleph.services.feature_flags import known_flag_keys


def test_retired_routes_are_absent_from_openapi() -> None:
    paths = create_app().openapi()["paths"]

    assert not any("flashcard" in path or "review" in path for path in paths)
    assert "/api/v1/settings" not in paths


def test_session_user_has_no_learner_settings_field() -> None:
    assert "settings" not in UserDTO.model_fields


def test_flashcards_flag_is_not_registered() -> None:
    assert "flashcards" not in known_flag_keys()
