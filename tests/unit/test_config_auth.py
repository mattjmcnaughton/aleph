"""Production hardening of the auth/session config (AL-020 review, TDD §7).

Red-green for the S1 finding: the dev ``session_secret_key`` default is a
truthy, repo-published value, so a startup guard keyed on emptiness can never
fire. The guard lives in ``config.py`` (the ``is_production`` model-validator
pattern) and rejects the dev secret, empty OIDC credentials, and — by forcing
it — insecure session cookies in production. Dev keeps its convenient defaults.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aleph.config import Settings

_VALID_PROD = {
    "env": "production",
    "public_base_url": "https://aleph.example",
    "session_secret_key": "a-real-random-64-char-production-secret",
    "oidc_issuer": "https://tenant.auth0.com",
    "oidc_client_id": "prod-client",
    "oidc_client_secret": "prod-secret",
}


def test_production_defaults_are_rejected() -> None:
    # The dev session secret is public in the repo; signing prod cookies with it
    # yields forgeable sessions. Startup must fail, not silently sign.
    with pytest.raises(ValidationError, match="session_secret_key"):
        Settings.model_validate({"env": "production"})


def test_production_rejects_the_dev_session_secret_explicitly() -> None:
    with pytest.raises(ValidationError, match="session_secret_key"):
        Settings.model_validate(
            {**_VALID_PROD, "session_secret_key": "dev-session-secret-change-me"}
        )


def test_production_rejects_empty_session_secret() -> None:
    with pytest.raises(ValidationError, match="session_secret_key"):
        Settings.model_validate({**_VALID_PROD, "session_secret_key": ""})


@pytest.mark.parametrize(
    "field", ["oidc_issuer", "oidc_client_id", "oidc_client_secret"]
)
def test_production_requires_oidc_credentials(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        Settings.model_validate({**_VALID_PROD, field: ""})


def test_valid_production_config_passes_and_forces_secure_cookie() -> None:
    settings = Settings.model_validate(_VALID_PROD)
    assert settings.is_production
    # Forced on in production regardless of the supplied value, so a deploy that
    # forgets SESSION_COOKIE_SECURE still marks the cookie Secure.
    assert settings.session_cookie_secure is True


def test_production_forces_secure_even_when_explicitly_false() -> None:
    settings = Settings.model_validate({**_VALID_PROD, "session_cookie_secure": False})
    assert settings.session_cookie_secure is True


def test_dev_defaults_are_unaffected() -> None:
    settings = Settings()
    assert settings.is_production is False
    assert settings.session_secret_key == "dev-session-secret-change-me"
    assert settings.session_cookie_secure is False


# --- AL-021: derived-admin domains (TDD §7/D14) ---------------------------


def test_admin_email_domains_default() -> None:
    assert Settings().admin_email_domain_set == frozenset({"mattjmcnaughton.com"})


def test_admin_email_domain_set_is_lowercased_stripped_and_deduped() -> None:
    settings = Settings(
        admin_email_domains=" MattJMcNaughton.com , aleph.test ,, aleph.test "
    )
    assert settings.admin_email_domain_set == frozenset(
        {"mattjmcnaughton.com", "aleph.test"}
    )


def test_admin_email_domain_set_is_empty_when_unset() -> None:
    assert Settings(admin_email_domains="").admin_email_domain_set == frozenset()


# --- AL-613: the ``client`` event dimension's map (TDD §12, D18) -------------- #


def test_oidc_client_map_defaults_to_the_dev_keycloak_native_client() -> None:
    # ``aleph-ios`` is the dev realm's native client (docker/keycloak); an
    # Auth0 client id is a random string, so the default cannot collide in prod.
    assert Settings.model_validate({}).oidc_client_labels == {"aleph-ios": "ios"}


def test_oidc_client_map_parses_several_entries() -> None:
    labels = Settings.model_validate(
        {"oidc_client_map": "aleph-ios:ios, AbC123xyz:ios ,other-app:watch"}
    ).oidc_client_labels

    assert labels == {"aleph-ios": "ios", "AbC123xyz": "ios", "other-app": "watch"}


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "no-separator",
        ":ios",  # no client id
        "aleph-ios:",  # no label
        "aleph-ios:iOS",  # labels are lowercase identifiers
        "aleph-ios:i os",
        "aleph-ios:ios:extra",
    ],
)
def test_oidc_client_map_drops_malformed_entries(raw: str) -> None:
    # Lenient like FEATURE_FLAG_DEFAULTS: a typo must not keep a deploy from
    # starting; the unmapped client then records as ``unknown``.
    assert Settings.model_validate({"oidc_client_map": raw}).oidc_client_labels == {}


def test_oidc_client_map_keeps_good_entries_beside_bad_ones() -> None:
    labels = Settings.model_validate(
        {"oidc_client_map": "garbage,aleph-ios:ios,,x:"}
    ).oidc_client_labels

    assert labels == {"aleph-ios": "ios"}
