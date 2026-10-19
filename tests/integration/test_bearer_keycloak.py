"""Bearer-token auth against a real compose-Keycloak access token.

The realm's dev/CI-only ``aleph-api-test`` client has the password grant, so a
test can mint a genuine ``aud=aleph-api`` access token for the realm's
published test users without a browser — the same token shape the native
``aleph-ios`` client gets from Authorization Code + PKCE. Everything below the
token is real: discovery, JWKS, signature, userinfo, Postgres provisioning.

Requires compose Keycloak (``just compose-keycloak-up``) and host Postgres, like
``test_auth_keycloak.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from aleph.config import settings
from tests.integration.conftest import build_authprobe_app
from tests.integration.test_auth_keycloak import APP_BASE_URL, _run_code_flow

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

API_AUDIENCE = "aleph-api"
TEST_CLIENT_ID = "aleph-api-test"


@pytest.fixture(autouse=True)
def enable_bearer_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "oidc_api_audience", API_AUDIENCE)


@pytest.fixture
async def app_client() -> AsyncGenerator[AsyncClient]:
    transport = ASGITransport(app=build_authprobe_app())
    async with AsyncClient(transport=transport, base_url=APP_BASE_URL) as client:
        yield client


async def _password_grant(username: str, password: str) -> dict[str, str]:
    async with AsyncClient() as keycloak:
        response = await keycloak.post(
            f"{settings.oidc_issuer.rstrip('/')}/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": TEST_CLIENT_ID,
                "username": username,
                "password": password,
                "scope": "openid profile email",
            },
        )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.anyio
async def test_keycloak_access_token_authenticates_api_requests(
    app_client: AsyncClient,
) -> None:
    tokens = await _password_grant("dev", "dev")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    probe = await app_client.get("/api/v1/_authprobe", headers=headers)
    session = await app_client.get("/api/v1/auth/session", headers=headers)

    assert probe.status_code == 200, probe.text
    assert session.status_code == 200, session.text
    body = session.json()
    assert body["authenticated"] is True
    assert body["user"]["id"] == probe.json()["id"]
    assert body["user"]["username"] == "dev"
    assert body["user"]["email"] == "dev@example.com"
    assert body["user"]["is_admin"] is False
    assert "set-cookie" not in probe.headers


@pytest.mark.anyio
async def test_keycloak_bearer_and_cookie_flow_share_one_account(
    app_client: AsyncClient,
) -> None:
    async with AsyncClient(follow_redirects=False) as keycloak_client:
        callback = await _run_code_flow(
            app_client, keycloak_client, username="dev", password="dev"
        )
    assert callback.status_code == 303
    cookie_id = (await app_client.get("/api/v1/_authprobe")).json()["id"]

    tokens = await _password_grant("dev", "dev")
    async with AsyncClient(
        transport=ASGITransport(app=build_authprobe_app()), base_url=APP_BASE_URL
    ) as bearer_client:
        probe = await bearer_client.get(
            "/api/v1/_authprobe",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert probe.json() == {"id": cookie_id}


@pytest.mark.anyio
async def test_keycloak_admin_and_unverified_users_classify_as_on_the_web(
    app_client: AsyncClient,
) -> None:
    admin = await _password_grant("admin-dev", "admin-dev")
    unverified = await _password_grant("unverified-dev", "unverified-dev")

    admin_session = await app_client.get(
        "/api/v1/auth/session",
        headers={"Authorization": f"Bearer {admin['access_token']}"},
    )
    unverified_session = await app_client.get(
        "/api/v1/auth/session",
        headers={"Authorization": f"Bearer {unverified['access_token']}"},
    )

    assert admin_session.json()["user"]["is_admin"] is True
    assert unverified_session.json()["user"]["email"] is None
    assert unverified_session.json()["user"]["is_admin"] is False


@pytest.mark.anyio
async def test_keycloak_id_token_is_not_an_api_access_token(
    app_client: AsyncClient,
) -> None:
    # A real, correctly signed token from the right issuer — but its ``aud`` is
    # the client id, not the API, so it must not authenticate.
    tokens = await _password_grant("dev", "dev")

    probe = await app_client.get(
        "/api/v1/_authprobe",
        headers={"Authorization": f"Bearer {tokens['id_token']}"},
    )

    assert probe.status_code == 401
