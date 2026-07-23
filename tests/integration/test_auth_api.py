"""Auth router behaviour with a stubbed OIDC client (AL-020).

Ported from habagou. These exercise provisioning, error handling, the ``401``
gate and logout without a real provider by stubbing the OAuth code exchange; the
end-to-end flow against compose Keycloak lives in ``test_auth_keycloak.py``.

The ``/api/v1/_authprobe`` route exists only for the test app: it is the
smallest possible consumer of ``get_current_user`` so the ``401`` gate can be
asserted before any real ``/api/v1/*`` route (AL-050/051) exists.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from aleph import db
from aleph.auth import AuthIdentity
from aleph.models import User
from tests.integration.conftest import build_authprobe_app

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


class StubOAuthClient:
    async def authorize_access_token(self, _request):
        return {"access_token": "stub"}


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient]:
    transport = ASGITransport(app=build_authprobe_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.anyio
async def test_callback_provisions_once_and_reuses_identity(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = AuthIdentity(
        issuer="https://issuer.example.test",
        subject="subject-1",
        username="dev",
        display_name="Dev User",
        email="dev@example.com",
    )
    monkeypatch.setattr(
        "aleph.routers.auth.oauth.create_client",
        lambda _provider: StubOAuthClient(),
    )
    monkeypatch.setattr("aleph.routers.auth.fetch_identity", lambda *_args: identity)

    first_response = await client.get("/auth/callback", follow_redirects=False)
    second_response = await client.get("/auth/callback", follow_redirects=False)

    assert first_response.status_code == 303
    assert second_response.status_code == 303
    async with db.async_session() as session:
        count = await session.scalar(
            select(func.count(User.id)).where(
                User.issuer == identity.issuer,
                User.subject == identity.subject,
            )
        )
        user = await session.scalar(
            select(User).where(
                User.issuer == identity.issuer,
                User.subject == identity.subject,
            )
        )

    assert count == 1
    assert user is not None
    assert user.username == "dev"
    assert user.display_name == "Dev User"
    assert user.email == "dev@example.com"
    assert "session" in client.cookies


@pytest.mark.anyio
async def test_callback_redirects_only_for_expected_auth_errors(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aleph.routers.auth.oauth.create_client",
        lambda _provider: StubOAuthClient(),
    )
    monkeypatch.setattr(
        "aleph.routers.auth.fetch_identity",
        lambda _token: (_ for _ in ()).throw(ValueError("missing claims")),
    )

    response = await client.get("/auth/callback", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=auth_failed"


@pytest.mark.anyio
async def test_callback_does_not_hide_unexpected_errors(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aleph.routers.auth.oauth.create_client",
        lambda _provider: StubOAuthClient(),
    )
    monkeypatch.setattr(
        "aleph.routers.auth.fetch_identity",
        lambda _token: (_ for _ in ()).throw(RuntimeError("database bug")),
    )

    with pytest.raises(RuntimeError, match="database bug"):
        await client.get("/auth/callback", follow_redirects=False)


@pytest.mark.anyio
async def test_api_v1_rejects_anonymous_with_401(client: AsyncClient) -> None:
    response = await client.get("/api/v1/_authprobe")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_401_uses_the_shared_error_envelope(client: AsyncClient) -> None:
    # The auth gate speaks the canonical envelope from day one (AL-050/051 and
    # the frontend consume ``error.code``); a plain ``{"detail": ...}`` would
    # diverge from every other API error.
    response = await client.get("/api/v1/_authprobe")

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "unauthenticated"
    assert body["error"]["message"] == "authentication required"
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]


async def _sign_in(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    identity: AuthIdentity,
) -> None:
    monkeypatch.setattr(
        "aleph.routers.auth.oauth.create_client",
        lambda _provider: StubOAuthClient(),
    )
    monkeypatch.setattr("aleph.routers.auth.fetch_identity", lambda *_args: identity)
    response = await client.get("/auth/callback", follow_redirects=False)
    assert response.status_code == 303


@pytest.mark.anyio
async def test_session_is_anonymous_for_signed_out_requests(
    client: AsyncClient,
) -> None:
    # The SPA root ``beforeLoad`` calls this signed-out; it must answer 200 with
    # ``authenticated: false`` (never 401), so the app can render the login gate.
    response = await client.get("/api/v1/auth/session")

    assert response.status_code == 200
    assert response.json() == {
        "authenticated": False,
        "provider": "keycloak",
        "user": None,
    }


@pytest.mark.anyio
async def test_session_payload_for_admin_user(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aleph.config import settings

    identity = AuthIdentity(
        issuer="https://issuer.example.test",
        subject="admin-subject",
        username="admin",
        display_name="Admin User",
        email="admin@mattjmcnaughton.com",
    )
    await _sign_in(client, monkeypatch, identity)

    response = await client.get("/api/v1/auth/session")

    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["provider"] == "keycloak"
    user = body["user"]
    assert user["username"] == "admin"
    assert user["display_name"] == "Admin User"
    assert user["email"] == "admin@mattjmcnaughton.com"
    assert user["is_admin"] is True
    # Admins get the picker options: bare model-id strings from the allowlist.
    assert user["model_allowlist"] == list(settings.allowlist_ids)
    # The id is the local account UUID, serialized as a string.
    uuid.UUID(user["id"])


@pytest.mark.anyio
async def test_session_payload_for_non_admin_user(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = AuthIdentity(
        issuer="https://issuer.example.test",
        subject="dev-subject",
        username="dev",
        display_name="Dev User",
        email="dev@example.com",
    )
    await _sign_in(client, monkeypatch, identity)

    response = await client.get("/api/v1/auth/session")

    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["provider"] == "keycloak"
    user = body["user"]
    assert user["username"] == "dev"
    assert user["is_admin"] is False
    # Non-admins never receive the picker options.
    assert user["model_allowlist"] == []


@pytest.mark.anyio
async def test_logout_clears_session(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = AuthIdentity(
        issuer="https://issuer.example.test",
        subject="logout-subject",
        username="logout-user",
        display_name="Logout User",
    )
    monkeypatch.setattr(
        "aleph.routers.auth.oauth.create_client",
        lambda _provider: StubOAuthClient(),
    )
    monkeypatch.setattr("aleph.routers.auth.fetch_identity", lambda *_args: identity)
    sign_in_response = await client.get("/auth/callback", follow_redirects=False)
    assert sign_in_response.status_code == 303
    assert (await client.get("/api/v1/_authprobe")).status_code == 200

    logout_response = await client.post("/auth/logout")
    probe_response = await client.get("/api/v1/_authprobe")

    assert logout_response.status_code == 204
    assert probe_response.status_code == 401


@pytest.mark.anyio
async def test_session_is_anonymous_when_cookied_user_was_deleted(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale session cookie (user row deleted) yields anonymous, never a 401."""
    identity = AuthIdentity(
        issuer="https://issuer.example.test",
        subject="deleted-subject",
        username="doomed",
        display_name="Doomed User",
        email="doomed@example.com",
    )
    await _sign_in(client, monkeypatch, identity)

    from sqlalchemy import delete

    from aleph import db
    from aleph.models import User

    async with db.async_session() as session:
        await session.execute(delete(User))
        await session.commit()

    response = await client.get("/api/v1/auth/session")

    assert response.status_code == 200
    assert response.json() == {
        "authenticated": False,
        "provider": "keycloak",
        "user": None,
    }
