"""Unit coverage for the admin model-picker enforcement helper (AL-052, §5.3).

The pure request-shape gate behind ``POST /api/v1/paths``: an override is
admin-only (403 otherwise) and allowlist-bound (422 off-allowlist). The DB-driven
routing (the override travelling on the persisted path row to the outline/lesson
model calls) is exercised in ``tests/integration/test_paths_api.py``; these pin
the config-free enforcement rules cheaply.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException, status

from aleph.dtos.paths import CreatePathRequest
from aleph.models import Level
from aleph.routers.v1.paths import resolve_model_overrides

ALLOWED = ("anthropic/claude-sonnet-5", "anthropic/claude-haiku-4-5")


def _request(**overrides: str) -> CreatePathRequest:
    return CreatePathRequest(topic="Rust ownership", level=Level.NEW_TO_IT, **overrides)


def test_no_override_is_allowed_for_anyone() -> None:
    result = resolve_model_overrides(_request(), is_admin=False, allowed=ALLOWED)
    assert result.model_outline is None
    assert result.model_lesson is None


def test_admin_allowlisted_override_passes_through() -> None:
    result = resolve_model_overrides(
        _request(
            model_outline="anthropic/claude-sonnet-5",
            model_lesson="anthropic/claude-haiku-4-5",
        ),
        is_admin=True,
        allowed=ALLOWED,
    )
    assert result.model_outline == "anthropic/claude-sonnet-5"
    assert result.model_lesson == "anthropic/claude-haiku-4-5"


def test_non_admin_override_raises_403() -> None:
    with pytest.raises(HTTPException) as exc:
        resolve_model_overrides(
            _request(model_lesson="anthropic/claude-haiku-4-5"),
            is_admin=False,
            allowed=ALLOWED,
        )
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN


def test_off_allowlist_override_raises_422() -> None:
    with pytest.raises(HTTPException) as exc:
        resolve_model_overrides(
            _request(model_outline="anthropic/claude-not-real"),
            is_admin=True,
            allowed=ALLOWED,
        )
    assert exc.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_authz_is_checked_before_allowlist() -> None:
    # A non-admin sending an off-allowlist id is a 403 (authz), not a 422 — the
    # capability gate runs first so a non-admin never learns the allowlist shape.
    with pytest.raises(HTTPException) as exc:
        resolve_model_overrides(
            _request(model_outline="anthropic/claude-not-real"),
            is_admin=False,
            allowed=ALLOWED,
        )
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
