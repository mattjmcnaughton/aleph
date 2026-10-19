"""The e2e fake OIDC issuer the iOS UI tests sign in through (iOS TDD D22, §16.3).

``scripts/e2e_fake_issuer.py`` serves discovery, JWKS and userinfo for a
committed, test-only signing key, and ``scripts/e2e_backend.create_stub_app``
mounts it only when ``E2E_FAKE_ISSUER=1`` and ``ENV=test``. The real
:class:`aleph.bearer.BearerAuthenticator` validates the pre-signed tokens
against it, so the XCUITest suite still proves real bearer validation.

These tests pin the two halves that matter for safety: the fake issuer works
end to end with the production verifier, and nothing about it can reach a
production configuration.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from joserfc.jwk import RSAKey
from pydantic import ValidationError

from aleph.bearer import (
    AccessTokenVerifier,
    BearerAuthenticator,
    InvalidBearerToken,
    OIDCProviderClient,
)
from aleph.config import Settings
from scripts import e2e_fake_issuer as fake


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def issuer_app() -> FastAPI:
    app = FastAPI()
    app.include_router(
        fake.fake_issuer_router(fake.FAKE_ISSUER, admin_domain="admin.test")
    )
    return app


def provider_over(app: FastAPI) -> OIDCProviderClient:
    """The production provider client, talking to the fake issuer in-process."""
    return OIDCProviderClient(
        f"{fake.FAKE_ISSUER}/.well-known/openid-configuration",
        transport=httpx.ASGITransport(app=app),
    )


def authenticator_over(app: FastAPI) -> BearerAuthenticator:
    provider = provider_over(app)
    return BearerAuthenticator(
        verifier=AccessTokenVerifier(
            issuer=fake.FAKE_ISSUER, audience=fake.FAKE_AUDIENCE, key_source=provider
        ),
        userinfo=provider,
    )


def committed_tokens() -> dict[str, str]:
    return json.loads(fake.TOKENS_PATH.read_text())


# --------------------------------------------------------------------------- #
# The committed key and tokens
# --------------------------------------------------------------------------- #


def test_the_committed_signing_key_is_an_rs256_signing_key() -> None:
    key = fake.load_signing_key()

    assert key.key_type == "RSA"
    assert key.kid == fake.SIGNING_KEY_ID
    published = fake.public_jwks(key)["keys"]
    assert len(published) == 1
    # The JWKS never leaks the private half.
    assert not {"d", "p", "q", "dp", "dq", "qi"} & published[0].keys()
    assert published[0]["alg"] == "RS256"
    assert published[0]["use"] == "sig"


def test_the_committed_tokens_are_what_the_mint_script_produces() -> None:
    """A drift check: re-minting from the committed key reproduces the fixture.

    RS256 (PKCS#1 v1.5) signatures are deterministic and every claim is fixed,
    so the file is a pure function of the key. A hand-edited token fails here.
    """
    minted = fake.render_tokens(fake.mint_tokens(fake.load_signing_key()))

    assert fake.TOKENS_PATH.read_text() == minted


def test_the_fixture_holds_a_learner_an_admin_and_an_expired_learner() -> None:
    assert set(committed_tokens()) == {"learner", "admin", "expiredLearner"}


# --------------------------------------------------------------------------- #
# The fake issuer through the production verifier
# --------------------------------------------------------------------------- #


def test_discovery_names_the_issuer_jwks_and_userinfo(issuer_app: FastAPI) -> None:
    response = TestClient(issuer_app).get(
        "/__e2e__/oidc/.well-known/openid-configuration"
    )

    assert response.status_code == 200
    assert response.json()["issuer"] == fake.FAKE_ISSUER
    assert response.json()["jwks_uri"] == f"{fake.FAKE_ISSUER}/jwks"
    assert response.json()["userinfo_endpoint"] == f"{fake.FAKE_ISSUER}/userinfo"


@pytest.mark.anyio
async def test_the_real_authenticator_accepts_the_learner_token(
    issuer_app: FastAPI,
) -> None:
    authentication = await authenticator_over(issuer_app).authenticate(
        committed_tokens()["learner"]
    )

    assert authentication.identity.issuer == fake.FAKE_ISSUER
    assert authentication.identity.subject == fake.LEARNER_SUBJECT
    assert authentication.identity.display_name == "E2E Learner"
    # The iOS client id, so the `client` event dimension reads `ios`.
    assert authentication.client_id == "aleph-ios"


@pytest.mark.anyio
async def test_the_admin_token_resolves_to_an_admin_domain_email(
    issuer_app: FastAPI,
) -> None:
    authentication = await authenticator_over(issuer_app).authenticate(
        committed_tokens()["admin"]
    )

    assert authentication.identity.subject == fake.ADMIN_SUBJECT
    assert authentication.identity.email == "e2e-admin@admin.test"


@pytest.mark.anyio
async def test_the_real_authenticator_rejects_the_expired_token(
    issuer_app: FastAPI,
) -> None:
    with pytest.raises(InvalidBearerToken):
        await authenticator_over(issuer_app).authenticate(
            committed_tokens()["expiredLearner"]
        )


@pytest.mark.anyio
async def test_userinfo_rejects_a_token_it_did_not_sign(issuer_app: FastAPI) -> None:
    stranger = RSAKey.generate_key(
        2048, parameters={"kid": fake.SIGNING_KEY_ID}, private=True
    )
    forged = fake.mint_token(
        stranger, fake.LEARNER_SUBJECT, expires_at=fake.VALID_UNTIL
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=issuer_app), base_url="http://e2e"
    ) as client:
        response = await client.get(
            "/__e2e__/oidc/userinfo", headers={"Authorization": f"Bearer {forged}"}
        )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_userinfo_without_a_token_is_401(issuer_app: FastAPI) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=issuer_app), base_url="http://e2e"
    ) as client:
        response = await client.get("/__e2e__/oidc/userinfo")

    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# Never in production
# --------------------------------------------------------------------------- #


def test_the_fake_issuer_is_on_only_when_the_env_var_is_exactly_1() -> None:
    assert fake.fake_issuer_requested({"E2E_FAKE_ISSUER": "1"})
    assert not fake.fake_issuer_requested({})
    assert not fake.fake_issuer_requested({"E2E_FAKE_ISSUER": "0"})
    assert not fake.fake_issuer_requested({"E2E_FAKE_ISSUER": "true"})


@pytest.mark.parametrize("env", ["development", "production"])
def test_configuring_the_fake_issuer_outside_env_test_is_refused(env: str) -> None:
    settings = Settings.model_validate(
        {
            "env": env,
            "session_secret_key": "a-real-random-64-char-production-secret",
            "oidc_issuer": "https://tenant.auth0.com/",
            "oidc_client_id": "prod-client",
            "oidc_client_secret": "prod-secret",
        }
    )

    with pytest.raises(RuntimeError, match="ENV=test"):
        fake.configure_fake_issuer(settings)
    assert settings.oidc_issuer == "https://tenant.auth0.com/"


@pytest.mark.parametrize("field", ["oidc_issuer", "oidc_metadata_url"])
def test_production_settings_reject_an_e2e_issuer(field: str) -> None:
    value = (
        fake.FAKE_ISSUER
        if field == "oidc_issuer"
        else f"{fake.FAKE_ISSUER}/.well-known/openid-configuration"
    )
    with pytest.raises(ValidationError, match="__e2e__"):
        Settings.model_validate(
            {
                "env": "production",
                "session_secret_key": "a-real-random-64-char-production-secret",
                "oidc_issuer": "https://tenant.auth0.com/",
                "oidc_client_id": "prod-client",
                "oidc_client_secret": "prod-secret",
                field: value,
            }
        )


def test_the_stub_app_mounts_the_fake_issuer_only_when_asked(
    restored_live_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.e2e_backend import create_stub_app

    restored_live_settings.env = "test"
    monkeypatch.setenv("E2E_FAKE_ISSUER", "1")

    app = create_stub_app()

    # No lifespan (no ``with``): the issuer's routes need no database.
    client = TestClient(app)
    assert client.get("/__e2e__/oidc/jwks").json() == fake.public_jwks(
        fake.load_signing_key()
    )
    assert client.get("/__e2e__/oidc/userinfo").status_code == 401
    assert restored_live_settings.oidc_issuer == fake.FAKE_ISSUER
    assert restored_live_settings.oidc_api_audience == fake.FAKE_AUDIENCE
    # Hidden from the schema, like the issuer it stands in for.
    assert not any(path.startswith("/__e2e__/oidc") for path in app.openapi()["paths"])


def test_the_stub_app_leaves_oidc_alone_without_the_env_var(
    restored_live_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.e2e_backend import create_stub_app

    monkeypatch.delenv("E2E_FAKE_ISSUER", raising=False)

    issuer_before = restored_live_settings.oidc_issuer
    audience_before = restored_live_settings.oidc_api_audience

    app = create_stub_app()

    assert TestClient(app).get("/__e2e__/oidc/jwks").status_code == 404
    assert restored_live_settings.oidc_issuer == issuer_before
    assert restored_live_settings.oidc_api_audience == audience_before
