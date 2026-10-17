"""Bearer-token requests resolve the same account as the cookie flow (real DB).

Tokens are minted with locally generated keys and verified through the real
``BearerAuthenticator`` over the in-memory JWKS fake (``tests/_bearer_fakes.py``)
— the provider is faked, Postgres is not. The same test against a real
Keycloak-issued token is ``test_bearer_keycloak.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from aleph import db
from aleph.auth import AuthIdentity
from aleph.bearer import get_bearer_authenticator
from aleph.config import settings
from aleph.models import User
from tests._bearer_fakes import ISSUER, USERINFO, fake_authenticator, mint
from tests.integration.conftest import build_authprobe_app

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping

    from fastapi import FastAPI


class StubOAuthClient:
    async def authorize_access_token(self, _request):
        return {"access_token": "stub"}


@pytest.fixture
def app() -> FastAPI:
    return build_authprobe_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


def use_userinfo(app: FastAPI, userinfo: Mapping[str, Any]) -> None:
    authenticator = fake_authenticator(userinfo)
    app.dependency_overrides[get_bearer_authenticator] = lambda: authenticator


def bearer(token: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token or mint()}"}


async def _accounts_for(subject: str) -> list[User]:
    async with db.async_session() as session:
        return list(
            await session.scalars(
                select(User).where(User.issuer == ISSUER, User.subject == subject)
            )
        )


@pytest.mark.anyio
async def test_first_bearer_request_provisions_then_reuses_the_account(
    app: FastAPI, client: AsyncClient
) -> None:
    use_userinfo(app, USERINFO)

    first = await client.get("/api/v1/_authprobe", headers=bearer())
    second = await client.get("/api/v1/_authprobe", headers=bearer())

    assert first.status_code == 200, first.text
    assert second.json() == first.json()
    [user] = await _accounts_for("subject-1")
    assert str(user.id) == first.json()["id"]
    assert user.username == "dev"
    assert user.display_name == "Dev User"
    assert user.email == "dev@example.com"
    # Nothing cookie-shaped happens for a bearer client.
    assert "set-cookie" not in first.headers
    assert not client.cookies


@pytest.mark.anyio
async def test_bearer_and_cookie_reach_the_same_account(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Sign in through the cookie flow first (stubbed code exchange), for the
    # same ``(issuer, subject)`` the bearer token will carry.
    monkeypatch.setattr(
        "aleph.routers.auth.oauth.create_client", lambda _p: StubOAuthClient()
    )
    monkeypatch.setattr(
        "aleph.routers.auth.fetch_identity",
        lambda *_a: AuthIdentity(
            issuer=ISSUER,
            subject="subject-1",
            username="dev",
            display_name="Dev User",
            email="dev@example.com",
        ),
    )
    await client.get("/auth/callback", follow_redirects=False)
    cookie_id = (await client.get("/api/v1/_authprobe")).json()["id"]

    use_userinfo(app, USERINFO)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as app_client:
        bearer_id = (
            await app_client.get("/api/v1/_authprobe", headers=bearer())
        ).json()

    assert bearer_id == {"id": cookie_id}
    async with db.async_session() as session:
        assert await session.scalar(select(func.count(User.id))) == 1


@pytest.mark.anyio
async def test_invalid_bearer_does_not_fall_back_to_a_valid_cookie(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "aleph.routers.auth.oauth.create_client", lambda _p: StubOAuthClient()
    )
    monkeypatch.setattr(
        "aleph.routers.auth.fetch_identity",
        lambda *_a: AuthIdentity(
            issuer=ISSUER,
            subject="subject-1",
            username="dev",
            display_name="Dev User",
            email="dev@example.com",
        ),
    )
    await client.get("/auth/callback", follow_redirects=False)
    assert (await client.get("/api/v1/_authprobe")).status_code == 200
    use_userinfo(app, USERINFO)

    probe = await client.get(
        "/api/v1/_authprobe", headers=bearer(mint(aud="somebody-else"))
    )
    session = await client.get(
        "/api/v1/auth/session", headers=bearer(mint(aud="somebody-else"))
    )

    assert probe.status_code == 401
    assert probe.json()["error"]["code"] == "unauthenticated"
    assert session.status_code == 401
    # The cookie itself is untouched and still works on its own.
    assert (await client.get("/api/v1/_authprobe")).status_code == 200


@pytest.mark.anyio
async def test_bearer_admin_gets_the_admin_session_payload(
    app: FastAPI, client: AsyncClient
) -> None:
    use_userinfo(
        app,
        {
            **USERINFO,
            "sub": "admin-subject",
            "preferred_username": "admin",
            "email": "admin@mattjmcnaughton.com",
        },
    )

    response = await client.get(
        "/api/v1/auth/session", headers=bearer(mint(sub="admin-subject"))
    )

    assert response.status_code == 200, response.text
    user = response.json()["user"]
    assert response.json()["authenticated"] is True
    assert user["is_admin"] is True
    assert user["model_allowlist"] == list(settings.allowlist_ids)
    assert isinstance(user["feature_flags"], dict)


@pytest.mark.anyio
async def test_unverified_bearer_email_is_never_admin(
    app: FastAPI, client: AsyncClient
) -> None:
    use_userinfo(
        app,
        {
            **USERINFO,
            "email": "admin@mattjmcnaughton.com",
            "email_verified": False,
        },
    )

    response = await client.get("/api/v1/auth/session", headers=bearer())

    user = response.json()["user"]
    assert user["email"] is None
    assert user["is_admin"] is False


@pytest.mark.anyio
async def test_changed_userinfo_claims_refresh_the_account(
    app: FastAPI, client: AsyncClient
) -> None:
    use_userinfo(app, USERINFO)
    await client.get("/api/v1/_authprobe", headers=bearer())

    # A new authenticator (fresh identity cache) sees the renamed profile.
    use_userinfo(app, {**USERINFO, "name": "Renamed", "email": "new@example.com"})
    await client.get("/api/v1/_authprobe", headers=bearer())

    [user] = await _accounts_for("subject-1")
    assert user.display_name == "Renamed"
    assert user.email == "new@example.com"
    assert user.username == "dev"


@pytest.mark.anyio
async def test_bearer_auth_off_rejects_every_bearer_token(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "oidc_api_audience", "")

    response = await client.get("/api/v1/_authprobe", headers=bearer())

    assert response.status_code == 401
    assert await _accounts_for("subject-1") == []
