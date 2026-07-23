"""Identity mapping and provider registration (ported from habagou, AL-020).

Pure claim-mapping and discovery-registration logic — no I/O, no Keycloak. The
end-to-end code flow against a real provider lives in
``tests/integration/test_auth_keycloak.py``.
"""

from __future__ import annotations

import pytest

from aleph import auth
from aleph.auth import AuthIdentity, fetch_identity
from aleph.config import Settings
from aleph.services.auth import _normalize_username


def test_registers_auth0_through_oidc_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered: dict[str, object] = {}
    monkeypatch.setattr(
        auth.oauth, "register", lambda **kwargs: registered.update(kwargs)
    )

    auth.register_provider(
        Settings(
            oidc_provider="auth0",
            oidc_issuer="https://tenant.auth0.com",
            oidc_client_id="client-id",
            oidc_client_secret="client-secret",
        )
    )

    assert registered == {
        "name": "auth0",
        "server_metadata_url": (
            "https://tenant.auth0.com/.well-known/openid-configuration"
        ),
        "client_id": "client-id",
        "client_secret": "client-secret",
        "client_kwargs": {"scope": "openid profile email"},
    }


def test_oidc_identity_uses_standard_claims() -> None:
    identity = fetch_identity(
        {
            "userinfo": {
                "iss": "http://127.0.0.1:18080/realms/aleph",
                "sub": "subject-1",
                "preferred_username": "dev",
                "name": "Dev User",
                "email": "dev@example.com",
            }
        },
    )

    assert identity == AuthIdentity(
        issuer="http://127.0.0.1:18080/realms/aleph",
        subject="subject-1",
        username="dev",
        display_name="Dev User",
        email="dev@example.com",
    )


def test_oidc_identity_requires_stable_claims() -> None:
    with pytest.raises(ValueError, match="required identity claims"):
        fetch_identity({"userinfo": {"preferred_username": "dev"}})


def test_normalize_username() -> None:
    assert _normalize_username(" Dev User ") == "dev-user"
    assert _normalize_username(" ") == "user"


def test_oidc_identity_drops_an_explicitly_unverified_email() -> None:
    # Admin classification (AL-021) will derive from the email domain, so an
    # email the provider itself marks unverified must never be stored — a
    # self-signup could otherwise mint an admin-domain address.
    identity = fetch_identity(
        {
            "userinfo": {
                "iss": "http://127.0.0.1:18080/realms/aleph",
                "sub": "subject-1",
                "preferred_username": "dev",
                "email": "anyone@mattjmcnaughton.com",
                "email_verified": False,
            }
        },
    )

    assert identity.email is None


def test_oidc_identity_keeps_email_when_verified_or_unstated() -> None:
    for extra in ({"email_verified": True}, {}):
        identity = fetch_identity(
            {
                "userinfo": {
                    "iss": "http://127.0.0.1:18080/realms/aleph",
                    "sub": "subject-1",
                    "preferred_username": "dev",
                    "email": "dev@example.com",
                    **extra,
                }
            },
        )
        assert identity.email == "dev@example.com"
