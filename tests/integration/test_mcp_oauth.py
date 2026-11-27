"""An MCP client connects through OAuth instead of a pasted key (real Postgres).

The client follows the MCP authorization spec from a bare ``401``: protected
resource metadata, authorization server metadata, then Authorization Code +
PKCE with a Client ID Metadata Document served by an in-memory fetcher, then
refresh-token rotation. The learner signs in through the real cookie flow
(stubbed OIDC exchange) and approves on the web client's consent API.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from aleph import db
from aleph.app import create_app
from aleph.auth import AuthIdentity
from aleph.config import settings
from aleph.models import ApiKey
from aleph.services import oauth

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import FastAPI

CLIENT_ID = "https://client.example/oauth/metadata.json"
REDIRECT = "http://127.0.0.1:53682/callback"
METADATA = {
    "client_id": CLIENT_ID,
    "client_name": "Example Agent",
    "redirect_uris": ["http://127.0.0.1/callback"],
    "token_endpoint_auth_method": "none",
}
ORIGIN = {"Origin": "http://testserver"}
LEARNER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="learner",
    username="learner",
    display_name="Learner",
    email="learner@example.com",
)


class FakeFetcher:
    def __init__(self, documents: Mapping[str, Mapping[str, Any]]) -> None:
        self.documents = documents

    async def fetch(self, url: str) -> Mapping[str, Any]:
        if url not in self.documents:
            raise oauth.OAuthError("invalid_client", "metadata could not be fetched")
        return self.documents[url]


class StubOAuthClient:
    async def authorize_access_token(self, _request: object) -> dict[str, str]:
        return {"access_token": "stub"}


@pytest.fixture
def app() -> FastAPI:
    app = create_app()
    fetcher = FakeFetcher({CLIENT_ID: METADATA})
    app.dependency_overrides[oauth.get_metadata_fetcher] = lambda: fetcher
    return app


@pytest.fixture
def mcp_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "feature_flag_defaults", "mcp:on")


@pytest.fixture(autouse=True)
def signed_in_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aleph.routers.auth.oauth.create_client", lambda _p: StubOAuthClient()
    )
    monkeypatch.setattr("aleph.routers.auth.fetch_identity", lambda *_a: LEARNER)


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _query(location: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}


def _params(challenge: str, **overrides: str) -> dict[str, str]:
    return {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "xyz",
        "resource": "http://testserver/mcp",
        "scope": "mcp",
        **overrides,
    }


async def _to_consent(browser: AsyncClient, challenge: str) -> dict[str, Any]:
    """Drive the browser to the consent page; return what it shows."""
    resp = await browser.get("/oauth/authorize", params=_params(challenge))
    if resp.headers["location"].startswith("/auth/login"):  # not signed in yet
        assert resp.headers["location"] == "/auth/login?oauth=1"
        callback = await browser.get("/auth/callback")
        resp = await browser.get(callback.headers["location"])
    assert resp.status_code == 303
    assert resp.headers["location"] == "/connect"
    consent = await browser.get("/api/v1/oauth/consent")
    assert consent.status_code == 200, consent.text
    return consent.json()


async def _approve(browser: AsyncClient, challenge: str) -> str:
    """Sign in, approve, and return the code the client receives."""
    shown = await _to_consent(browser, challenge)
    decided = await browser.post(
        "/api/v1/oauth/consent",
        json={"id": shown["id"], "approve": True},
        headers=ORIGIN,
    )
    assert decided.status_code == 200, decided.text
    location = decided.json()["redirect_to"]
    assert location.startswith(REDIRECT + "?")
    query = _query(location)
    assert (query["state"], query["iss"]) == ("xyz", "http://testserver")
    return query["code"]


async def _token(client: AsyncClient, **form: str) -> Any:
    return await client.post(
        "/oauth/token",
        data={"client_id": CLIENT_ID, "resource": "http://testserver/mcp", **form},
    )


async def _exchange(client: AsyncClient, code: str, verifier: str) -> Any:
    return await _token(
        client,
        grant_type="authorization_code",
        code=code,
        code_verifier=verifier,
        redirect_uri=REDIRECT,
    )


async def _connect(app: FastAPI, browser: AsyncClient) -> dict[str, Any]:
    verifier, challenge = _pkce()
    code = await _approve(browser, challenge)
    async with _client(app) as agent:
        resp = await _exchange(agent, code, verifier)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _mcp(app: FastAPI, token: str) -> Any:
    async with _client(app) as agent:
        return await agent.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": f"Bearer {token}"},
        )


@pytest.mark.anyio
async def test_a_client_discovers_signs_in_and_drives_mcp_with_the_token(
    app: FastAPI, mcp_on: None
) -> None:
    async with _client(app) as agent:
        bare = await agent.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}
        )
        assert bare.status_code == 401
        prm_url = bare.headers["www-authenticate"].split('"')[1]
        prm = (await agent.get(prm_url)).json()
        # Clients that probe the path-suffixed URL first find the same document.
        suffixed = await agent.get("/.well-known/oauth-protected-resource/mcp")
        assert suffixed.json() == prm
        assert prm["resource"] == "http://testserver/mcp"
        [issuer] = prm["authorization_servers"]
        asm = (
            await agent.get(f"{issuer}/.well-known/oauth-authorization-server")
        ).json()
        assert asm["issuer"] == issuer
        assert asm["code_challenge_methods_supported"] == ["S256"]
        assert asm["client_id_metadata_document_supported"] is True
        assert "none" in asm["token_endpoint_auth_methods_supported"]
        assert "refresh_token" in asm["grant_types_supported"]

    verifier, challenge = _pkce()
    async with _client(app) as browser:
        shown = await _to_consent(browser, challenge)
        assert shown["client_name"] == "Example Agent"
        assert shown["client_host"] == "client.example"
        assert shown["redirect_host"] == "127.0.0.1:53682"
        assert shown["loopback_only"] is True
        decided = await browser.post(
            "/api/v1/oauth/consent",
            json={"id": shown["id"], "approve": True},
            headers=ORIGIN,
        )
        code = _query(decided.json()["redirect_to"])["code"]
        async with _client(app) as agent:
            token = await _exchange(agent, code, verifier)
        listed = (await browser.get("/api/v1/api-keys")).json()

    assert token.status_code == 200, token.text
    assert token.headers["cache-control"] == "no-store"
    body = token.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600
    assert body["refresh_token"] != body["access_token"]
    assert (await _mcp(app, body["access_token"])).json()["result"]["tools"]
    async with _client(app) as agent:
        direct = await agent.get(
            "/api/v1/paths", headers={"Authorization": f"Bearer {body['access_token']}"}
        )
    assert direct.status_code == 401  # like any API key: only through /mcp
    # Listed as a connected app, not among hand-made keys.
    assert listed["keys"] == []
    [connection] = listed["connections"]
    assert (connection["name"], connection["client_host"]) == (
        "Example Agent",
        "client.example",
    )


@pytest.mark.anyio
async def test_refresh_rotates_and_a_replayed_refresh_token_disconnects(
    app: FastAPI, mcp_on: None
) -> None:
    async with _client(app) as browser:
        first = await _connect(app, browser)
        async with _client(app) as agent:
            wrong_client = await _token(
                agent,
                grant_type="refresh_token",
                refresh_token=first["refresh_token"],
                client_id="https://other.example/c.json",
            )
            second = await _token(
                agent, grant_type="refresh_token", refresh_token=first["refresh_token"]
            )
        assert wrong_client.json()["error"] == "invalid_grant"
        assert second.status_code == 200, second.text
        second = second.json()
        assert second["refresh_token"] != first["refresh_token"]
        # The old access token is gone; the new one works.
        assert (await _mcp(app, first["access_token"])).status_code == 401
        assert (await _mcp(app, second["access_token"])).status_code == 200

        # The replaced refresh token comes back: stolen, so disconnect.
        async with _client(app) as agent:
            replay = await _token(
                agent, grant_type="refresh_token", refresh_token=first["refresh_token"]
            )
            after = await _token(
                agent, grant_type="refresh_token", refresh_token=second["refresh_token"]
            )
        listed = (await browser.get("/api/v1/api-keys")).json()

    assert replay.json()["error"] == "invalid_grant"
    assert after.json()["error"] == "invalid_grant"
    assert (await _mcp(app, second["access_token"])).status_code == 401
    assert listed["connections"] == []


@pytest.mark.anyio
async def test_an_expired_access_token_is_challenged_and_refresh_recovers(
    app: FastAPI, mcp_on: None
) -> None:
    async with _client(app) as browser:
        tokens = await _connect(app, browser)
    async with db.async_session() as session:
        await session.execute(
            update(ApiKey).values(expires_at=ApiKey.created_at - oauth.CODE_TTL)
        )
        await session.commit()

    expired = await _mcp(app, tokens["access_token"])
    assert expired.status_code == 401
    challenge = expired.headers["www-authenticate"]
    assert challenge.startswith('Bearer error="invalid_token"')
    assert "resource_metadata=" in challenge
    async with _client(app) as agent:
        renewed = await _token(
            agent, grant_type="refresh_token", refresh_token=tokens["refresh_token"]
        )
    assert (await _mcp(app, renewed.json()["access_token"])).status_code == 200


@pytest.mark.anyio
async def test_a_replayed_code_fails_and_revokes_what_it_issued(
    app: FastAPI, mcp_on: None
) -> None:
    verifier, challenge = _pkce()
    async with _client(app) as browser:
        code = await _approve(browser, challenge)
    async with _client(app) as agent:
        issued = (await _exchange(agent, code, verifier)).json()
        replay = await _exchange(agent, code, verifier)
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"
    assert (await _mcp(app, issued["access_token"])).status_code == 401


@pytest.mark.anyio
async def test_a_wrong_verifier_or_redirect_gets_no_token(
    app: FastAPI, mcp_on: None
) -> None:
    verifier, challenge = _pkce()
    async with _client(app) as browser:
        code = await _approve(browser, challenge)
        wrong_verifier = await _exchange(browser, code, _pkce()[0])
        code = await _approve(browser, challenge)
        wrong_redirect = await _token(
            browser,
            grant_type="authorization_code",
            code=code,
            code_verifier=verifier,
            redirect_uri="http://127.0.0.1:1/callback",
        )
        listed = (await browser.get("/api/v1/api-keys")).json()
    assert wrong_verifier.json()["error"] == "invalid_grant"
    assert wrong_redirect.json()["error"] == "invalid_grant"
    assert listed["connections"] == []


@pytest.mark.anyio
async def test_an_unlisted_redirect_or_unknown_client_is_never_redirected_to(
    app: FastAPI, mcp_on: None
) -> None:
    _, challenge = _pkce()
    async with _client(app) as browser:
        await browser.get("/auth/callback")
        evil = await browser.get(
            "/oauth/authorize",
            params=_params(challenge, redirect_uri="https://evil.example/callback"),
        )
        unknown = await browser.get(
            "/oauth/authorize",
            params=_params(challenge, client_id="https://other.example/c.json"),
        )
        not_a_url = await browser.get(
            "/oauth/authorize", params=_params(challenge, client_id="claude")
        )
    assert evil.headers["location"] == "/connect?error=invalid_redirect"
    assert unknown.headers["location"] == "/connect?error=invalid_client"
    assert not_a_url.headers["location"] == "/connect?error=invalid_client"


@pytest.mark.anyio
async def test_without_the_mcp_flag_the_learner_is_sent_back_denied(
    app: FastAPI,
) -> None:
    _, challenge = _pkce()
    async with _client(app) as browser:
        await browser.get("/auth/callback")
        resp = await browser.get("/oauth/authorize", params=_params(challenge))
        consent = await browser.get("/api/v1/oauth/consent")
    query = _query(resp.headers["location"])
    assert resp.headers["location"].startswith(REDIRECT)
    assert (query["error"], query["state"]) == ("access_denied", "xyz")
    assert consent.status_code == 404


@pytest.mark.anyio
async def test_consent_refuses_denials_stale_requests_and_other_origins(
    app: FastAPI, mcp_on: None
) -> None:
    _, challenge = _pkce()
    async with _client(app) as browser:
        shown = await _to_consent(browser, challenge)
        cross = await browser.post(
            "/api/v1/oauth/consent",
            json={"id": shown["id"], "approve": True},
            headers={"Origin": "https://evil.example"},
        )
        stale = await browser.post(
            "/api/v1/oauth/consent",
            json={"id": "an-older-request", "approve": True},
            headers=ORIGIN,
        )
        denied = await browser.post(
            "/api/v1/oauth/consent",
            json={"id": shown["id"], "approve": False},
            headers=ORIGIN,
        )
        again = await browser.get("/api/v1/oauth/consent")
    assert cross.status_code == 403
    assert stale.status_code == 409
    query = _query(denied.json()["redirect_to"])
    assert (query["error"], query["state"]) == ("access_denied", "xyz")
    assert "code" not in query
    assert again.status_code == 404  # answered once


@pytest.mark.anyio
async def test_approvals_are_rate_limited_per_learner(
    app: FastAPI, mcp_on: None
) -> None:
    _, challenge = _pkce()
    async with _client(app) as browser:
        for _ in range(oauth.APPROVALS_PER_HOUR):
            await _approve(browser, challenge)
        shown = await _to_consent(browser, challenge)
        limited = await browser.post(
            "/api/v1/oauth/consent",
            json={"id": shown["id"], "approve": True},
            headers=ORIGIN,
        )
    assert limited.status_code == 429


@pytest.mark.anyio
async def test_disconnecting_in_settings_ends_access_and_refresh(
    app: FastAPI, mcp_on: None
) -> None:
    async with _client(app) as browser:
        tokens = await _connect(app, browser)
        [connection] = (await browser.get("/api/v1/api-keys")).json()["connections"]
        revoked = await browser.delete(
            f"/api/v1/api-keys/{connection['id']}", headers=ORIGIN
        )
    assert revoked.status_code == 204
    assert (await _mcp(app, tokens["access_token"])).status_code == 401
    async with _client(app) as agent:
        refreshed = await _token(
            agent, grant_type="refresh_token", refresh_token=tokens["refresh_token"]
        )
    assert refreshed.json()["error"] == "invalid_grant"
