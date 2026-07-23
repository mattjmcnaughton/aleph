"""End-to-end OIDC authorization-code flow against compose Keycloak (AL-020).

This is the acceptance test for D2: it drives the *real* provider — no stubbed
OAuth client — the way habagou's Playwright e2e helper does, but scripted over
HTTP instead of a browser:

1. ``GET /auth/login`` on the in-process ASGI app returns a 302 to Keycloak's
   authorization endpoint (authlib stashes state/nonce in the session cookie).
2. A second, real-network client fetches Keycloak's login page and posts the
   realm test user's credentials; Keycloak 302s back to ``/auth/callback`` with
   an authorization ``code``.
3. ``GET /auth/callback`` on the ASGI app exchanges the code (a real
   server-to-server call to Keycloak), provisions the local user, and sets the
   session cookie.

Requires compose Keycloak (``just compose-keycloak-up``) and host Postgres. Not
marked ``external``: like the Postgres-backed repository tests, Keycloak is a
required local compose service, so this runs as part of ``just test-integration``
/ ``gate-expensive`` (CI provisions it). ``external`` is reserved for paid
third-party services (OpenRouter live smoke).
"""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from aleph import db
from aleph.config import settings
from aleph.models import User
from tests.integration.conftest import build_authprobe_app

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# Must be a redirect URI registered for the ``aleph`` client in the realm.
APP_BASE_URL = "http://localhost:8000"

_LOGIN_FORM_ACTION = re.compile(
    r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"', re.IGNORECASE
)


def _cookie_header(client: AsyncClient) -> str:
    """Serialize the client's cookie jar into a ``Cookie`` request header.

    Keycloak marks its login-flow cookies (``AUTH_SESSION_ID``, ``KC_RESTART``)
    ``Secure`` because they are ``SameSite=None``. Browsers treat
    ``http://localhost`` / ``http://127.0.0.1`` as secure contexts and send them
    over plain HTTP anyway; httpx's cookie jar withholds ``Secure`` cookies from
    non-TLS requests. Sending them explicitly mirrors the browser's behaviour so
    the scripted flow reaches Keycloak the way the real login does.
    """
    return "; ".join(f"{name}={value}" for name, value in client.cookies.items())


@pytest.fixture
async def app_client() -> AsyncGenerator[AsyncClient]:
    """Client bound to the in-process ASGI app; carries the app session cookie."""
    transport = ASGITransport(app=build_authprobe_app())
    async with AsyncClient(transport=transport, base_url=APP_BASE_URL) as client:
        yield client


@pytest.fixture
async def keycloak_client() -> AsyncGenerator[AsyncClient]:
    """Real-network client to Keycloak; carries Keycloak's login cookies."""
    async with AsyncClient(follow_redirects=False) as client:
        yield client


async def _run_code_flow(
    app_client: AsyncClient,
    keycloak_client: AsyncClient,
    *,
    username: str,
    password: str,
) -> httpx.Response:
    """Drive the full authorization-code flow, returning the callback response."""
    login = await app_client.get("/auth/login", follow_redirects=False)
    assert login.status_code == 302, login.text
    authorize_url = login.headers["location"]
    assert authorize_url.startswith(settings.oidc_issuer), authorize_url

    form_page = await keycloak_client.get(authorize_url, follow_redirects=True)
    assert form_page.status_code == 200, form_page.text
    match = _LOGIN_FORM_ACTION.search(form_page.text)
    assert match is not None, "Keycloak login form not found"
    action_url = html.unescape(match.group(1))

    submitted = await keycloak_client.post(
        action_url,
        data={"username": username, "password": password},
        headers={"cookie": _cookie_header(keycloak_client)},
    )
    assert submitted.status_code in (302, 303), submitted.text
    callback_url = submitted.headers["location"]
    assert callback_url.startswith(f"{APP_BASE_URL}/auth/callback"), callback_url

    return await app_client.get(callback_url, follow_redirects=False)


@pytest.mark.anyio
async def test_code_flow_provisions_user_and_sets_cookie(
    app_client: AsyncClient,
    keycloak_client: AsyncClient,
) -> None:
    callback = await _run_code_flow(
        app_client, keycloak_client, username="dev", password="dev"
    )

    assert callback.status_code == 303
    assert callback.headers["location"] == "/"
    # The signed session cookie is set and the local user row exists.
    assert "session" in app_client.cookies

    async with db.async_session() as session:
        user = await session.scalar(
            select(User).where(
                User.issuer == settings.oidc_issuer,
                User.email == "dev@example.com",
            )
        )

    assert user is not None
    assert user.username == "dev"
    assert user.display_name == "Dev User"
    assert user.subject  # Keycloak's stable subject id

    # The cookie authenticates a real /api/v1/* request end to end.
    probe = await app_client.get("/api/v1/_authprobe")
    assert probe.status_code == 200
    assert probe.json() == {"id": str(user.id)}


@pytest.mark.anyio
async def test_code_flow_drops_unverified_email(
    app_client: AsyncClient,
    keycloak_client: AsyncClient,
) -> None:
    callback = await _run_code_flow(
        app_client,
        keycloak_client,
        username="unverified-dev",
        password="unverified-dev",
    )

    assert callback.status_code == 303
    async with db.async_session() as session:
        user = await session.scalar(
            select(User).where(
                User.issuer == settings.oidc_issuer,
                User.username == "unverified-dev",
            )
        )

    assert user is not None
    # Keycloak reports email_verified:false, so the email is dropped (TDD §7).
    assert user.email is None
