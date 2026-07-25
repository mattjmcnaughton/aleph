"""The deploy artifacts and the production config say the same thing (AL-100).

`docs/deploy.md` tells a human which secrets to set with `flyctl secrets set`
(AL-101–103), and `fly.toml` says which configuration is safe to commit. Both are
prose about `Settings`, and prose drifts. This pins the three ways that drift bites:

1. every secret the runbook calls **required** is actually documented there, and
   every one the *guard* enforces is on that list — so neither side can grow an
   entry the other has not heard of;
2. the fail-fast subset really does fail fast (the guard, not the doc, is the
   authority on what "required" means); and
3. `fly.toml` `[env]` commits no secret — a leak this repo would otherwise only
   catch by reading the file.

The guard's *behaviour* (dev default rejected, `Secure` forced) is covered by
`test_config_auth.py`; this file is about the artifacts agreeing with it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from aleph.config import Settings

# ``tests/unit/test_deploy_config.py`` → repo root is three parents up.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEPLOY_DOC = _REPO_ROOT / "docs" / "deploy.md"
_FLY_TOML = _REPO_ROOT / "fly.toml"

# Secrets a production deploy must set (docs/deploy.md § Secrets → Required).
# ``DATABASE_URL`` and ``OPENROUTER_API_KEY`` have workable-looking defaults and so
# cannot fail fast — they are required because the defaults point at nothing real
# (localhost) and at no credential.
REQUIRED_SECRETS = frozenset(
    {
        "DATABASE_URL",
        "SESSION_SECRET_KEY",
        "OPENROUTER_API_KEY",
        "OIDC_ISSUER",
        "OIDC_CLIENT_ID",
        "OIDC_CLIENT_SECRET",
    }
)

# The subset ``config._enforce_production_auth`` rejects at startup.
FAIL_FAST_SECRETS = frozenset(
    {"SESSION_SECRET_KEY", "OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET"}
)

# A relation between two constants in this file, not behaviour — so it is checked
# once at import rather than dressed up as a test: a secret the guard enforces but
# the runbook never mentions is a secret nobody can set.
assert FAIL_FAST_SECRETS <= REQUIRED_SECRETS, (
    "fail-fast secrets missing from the runbook table: "
    f"{sorted(FAIL_FAST_SECRETS - REQUIRED_SECRETS)}"
)

OPTIONAL_SECRETS = frozenset({"LOGFIRE_TOKEN"})

# A production configuration with every required secret supplied.
_VALID_PROD = {
    "env": "production",
    "database_url": "postgresql+asyncpg://u:p@ep-x-pooler.neon.tech/db?ssl=require",
    "session_secret_key": "a-real-random-production-secret",
    "openrouter_api_key": "sk-or-not-a-real-key",
    "oidc_issuer": "https://tenant.auth0.com/",
    "oidc_client_id": "prod-client",
    "oidc_client_secret": "prod-secret",
}


def _deploy_doc() -> str:
    return _DEPLOY_DOC.read_text(encoding="utf-8")


def _fly_config() -> dict:
    return tomllib.loads(_FLY_TOML.read_text(encoding="utf-8"))


# The tail of ``_enforce_production_auth``'s error, up to the field list.
_GUARD_PREFIX = "requires real auth secrets; set a non-default value for: "


def _fields_named_by_guard(message: str) -> frozenset[str]:
    """The field names ``_enforce_production_auth`` listed as missing."""
    _, marker, tail = message.partition(_GUARD_PREFIX)
    assert marker, f"the guard's message changed shape: {message}"
    listed, _, _ = tail.partition(".")
    return frozenset(name.strip() for name in listed.split(",") if name.strip())


def _production_settings_with_every_string_blank() -> dict[str, object]:
    """A production payload with *every* string setting emptied.

    Blanking only the known secrets would ask the guard about the fields we
    already know about. Blanking everything means any field a future edit adds to
    the guard is empty too, and therefore shows up in the message.
    """
    blank = {
        name: ""
        for name, field in Settings.model_fields.items()
        if field.annotation is str
    }
    return {**blank, "env": "production"}


@pytest.mark.parametrize("secret", sorted(REQUIRED_SECRETS | OPTIONAL_SECRETS))
def test_every_secret_is_documented(secret: str) -> None:
    assert secret in _deploy_doc(), f"{secret} is not documented in docs/deploy.md"


@pytest.mark.parametrize("secret", sorted(REQUIRED_SECRETS))
def test_every_required_secret_names_the_ticket_that_sets_it(secret: str) -> None:
    # AL-101/102/103 are the human provisioning tickets; a required secret with no
    # owner is a secret nobody sets.
    doc = _deploy_doc()
    row = next(line for line in doc.splitlines() if line.startswith(f"| `{secret}`"))
    assert any(ticket in row for ticket in ("AL-101", "AL-102", "AL-103")), row


def test_a_fully_configured_production_settings_validates() -> None:
    settings = Settings.model_validate(_VALID_PROD)
    assert settings.is_production
    assert settings.session_cookie_secure is True


@pytest.mark.parametrize("secret", sorted(FAIL_FAST_SECRETS))
def test_omitting_a_fail_fast_secret_raises_at_startup(secret: str) -> None:
    without = {**_VALID_PROD, secret.lower(): ""}
    with pytest.raises(ValidationError, match=secret.lower()):
        Settings.model_validate(without)


def test_the_guard_enforces_exactly_the_documented_fail_fast_set() -> None:
    """The guard and ``FAIL_FAST_SECRETS`` name the same fields, both ways.

    The test above pins *listed ⇒ enforced*: every secret this file calls
    fail-fast really does raise. It cannot catch the other direction — a field
    added to ``_enforce_production_auth`` would be enforced in production while
    ``FAIL_FAST_SECRETS``, and therefore the runbook table and its "four of those
    six" prose, never heard of it. Operators would meet the new requirement as a
    failed deploy. So: empty every string setting, and require the guard's own
    message to name this set exactly — nothing extra, nothing missing.
    """
    with pytest.raises(ValidationError) as excinfo:
        Settings.model_validate(_production_settings_with_every_string_blank())

    assert _fields_named_by_guard(str(excinfo.value)) == frozenset(
        secret.lower() for secret in FAIL_FAST_SECRETS
    )


def test_fly_env_commits_no_secret() -> None:
    committed = set(_fly_config()["env"])
    leaked = committed & (REQUIRED_SECRETS | OPTIONAL_SECRETS)
    assert not leaked, f"fly.toml [env] commits secrets: {sorted(leaked)}"


def test_fly_env_arms_the_production_guards() -> None:
    # Without ENV=production the guards above are inert: the stub model becomes
    # reachable and the dev session secret is accepted. It is committed config
    # precisely so it cannot be forgotten.
    assert _fly_config()["env"]["ENV"] == "production"


def test_fly_config_matches_the_image() -> None:
    fly = _fly_config()
    assert fly["app"] == "aleph"
    assert fly["build"]["build-target"] == "production"
    # The Dockerfile EXPOSEs 8000 and uvicorn binds it; Fly must route there.
    assert fly["http_service"]["internal_port"] == 8000
    # TDD §13: MVP runs a single machine.
    assert fly["http_service"]["max_machines_running"] == 1
    # The release command is the migration script the image ships.
    assert fly["deploy"]["release_command"] == "/app/docker/release.sh"
