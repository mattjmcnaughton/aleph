"""Unit tests for the AL-030 model-routing config slots (TDD §5.3, §14).

New file (AL-030) so it never collides with other tickets editing ``test_config``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aleph.config import Settings


def test_model_slots_default_to_sonnet() -> None:
    # TDD §14: all three slots start at one strong model, no premature tiering.
    settings = Settings()
    assert settings.model_outline == "anthropic/claude-sonnet-5"
    assert settings.model_lesson == "anthropic/claude-sonnet-5"
    assert settings.model_judge == "anthropic/claude-sonnet-5"


def test_allowlist_default_and_parsing() -> None:
    settings = Settings()
    ids = settings.allowlist_ids
    # The §14 default: the starting model plus the four refinement candidates.
    assert ids == (
        "anthropic/claude-sonnet-5",
        "anthropic/claude-haiku-4-5",
        "anthropic/claude-opus-4-8",
        "openai/gpt-5.6-terra",
        "minimax/minimax-m3",
    )


def test_allowlist_parsing_trims_and_drops_empties() -> None:
    settings = Settings(model_allowlist=" a/b ,, c/d , ")
    assert settings.allowlist_ids == ("a/b", "c/d")


def test_stub_allowed_outside_production() -> None:
    # The stub is the CI/e2e model (D9); it must be selectable in dev/CI.
    settings = Settings(env="development", model_outline="stub", model_lesson="stub")
    assert settings.model_outline == "stub"


@pytest.mark.parametrize("slot", ["model_outline", "model_lesson", "model_judge"])
def test_stub_rejected_in_production(slot: str) -> None:
    # Config guard: the deterministic stub must never resolve in production.
    # ``model_validate`` takes a dict (dynamic slot key), triggering the same
    # after-validator that ``Settings(...)`` would.
    with pytest.raises(ValidationError, match="stub"):
        Settings.model_validate({"env": "production", slot: "stub"})


def test_non_stub_production_config_is_fine() -> None:
    # AL-020 added a production auth guard: a real deploy needs real secrets, so
    # the model-routing happy path must supply them too.
    settings = Settings(
        env="production",
        model_outline="anthropic/claude-sonnet-5",
        session_secret_key="a-real-random-production-secret",
        oidc_issuer="https://tenant.auth0.com",
        oidc_client_id="prod-client",
        oidc_client_secret="prod-secret",
    )
    assert settings.env == "production"


def test_stub_in_allowlist_rejected_in_production() -> None:
    # The guard must cover MODEL_ALLOWLIST, not just the three fixed slots: once
    # AL-052's picker lands, an allowlisted ``stub`` would let an admin select it
    # in prod and reach ``resolve_model("stub")``.
    with pytest.raises(ValidationError, match="model_allowlist"):
        Settings.model_validate(
            {
                "env": "production",
                "model_allowlist": "anthropic/claude-sonnet-5,stub",
            }
        )


def test_stub_in_allowlist_allowed_outside_production() -> None:
    settings = Settings(env="test", model_allowlist="anthropic/claude-sonnet-5,stub")
    assert "stub" in settings.allowlist_ids


@pytest.mark.parametrize("bad_env", ["prod", "PRODUCTION", "staging", ""])
def test_invalid_env_value_is_rejected(bad_env: str) -> None:
    # ``env`` is a closed Literal: a typo like ENV=prod must fail loudly at
    # startup, not silently count as non-production and disable the stub guard.
    with pytest.raises(ValidationError):
        Settings.model_validate({"env": bad_env})
