"""Unit tests for the AL-030 model-routing config slots (TDD §5.3, §14).

New file (AL-030) so it never collides with other tickets editing ``test_config``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aleph.config import MODEL_SLOTS, Settings


def test_models_default_to_gpt_6_luna() -> None:
    # One app-wide model.
    settings = Settings()
    assert {slot: getattr(settings, slot) for slot in MODEL_SLOTS} == {
        "model_default": "openai/gpt-6-luna",
    }


def test_allowlist_default_and_parsing() -> None:
    settings = Settings()
    ids = settings.allowlist_ids
    # The default model is first; every entry exists on OpenRouter.
    assert ids == (
        "openai/gpt-6-luna",
        "openai/gpt-6.2-sol",
        "anthropic/claude-haiku-5.5",
        "anthropic/claude-sonnet-5.5",
        "anthropic/claude-opus-5.5",
    )


def test_allowlist_parsing_trims_and_drops_empties() -> None:
    settings = Settings(model_allowlist=" a/b ,, c/d , ")
    assert settings.allowlist_ids == ("a/b", "c/d")


def test_stub_allowed_outside_production() -> None:
    # The stub is the CI/e2e model (D9); it must be selectable in dev/CI.
    settings = Settings(env="development", model_default="stub")
    assert settings.model_default == "stub"


# Spelled out rather than parametrized over ``config.MODEL_SLOTS``: the point is
# to catch a slot dropped from that constant, which parametrizing over it could
# never do (the case would vanish with the entry).
@pytest.mark.parametrize(
    "slot",
    [
        "model_default",
    ],
)
def test_stub_rejected_in_production(slot: str) -> None:
    # Config guard: the deterministic stub must never resolve in production.
    # ``model_validate`` takes a dict (dynamic slot key), triggering the same
    # after-validator that ``Settings(...)`` would.
    #
    # The regex matches the guard's own wording *and* names the slot. A bare
    # ``match="stub"`` passed vacuously: pydantic echoes the offending input
    # (``input_value={'env': 'production', <slot>: 'stub'}``) into the rendered
    # error, so any *other* production validator firing would satisfy it — and a
    # slot missing from the offenders tuple would go undetected.
    with pytest.raises(ValidationError, match=rf"not allowed in production.+{slot}"):
        Settings.model_validate({"env": "production", slot: "stub"})


def test_non_stub_production_config_is_fine() -> None:
    # AL-020 added a production auth guard: a real deploy needs real secrets, so
    # the model-routing happy path must supply them too.
    settings = Settings(
        env="production",
        model_default="anthropic/claude-sonnet-5.5",
        public_base_url="https://aleph.example",
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
