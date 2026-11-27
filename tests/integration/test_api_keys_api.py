"""API keys: minted in Settings, accepted only through ``/mcp`` (real Postgres).

The learner signs in with the real cookie flow (a stubbed OIDC code exchange,
as in ``test_lessons_api``) and creates a key; an MCP client then calls
``/mcp`` with only that key. No OIDC bearer setup is involved.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from aleph import db
from aleph.app import create_app
from aleph.auth import AuthIdentity
from aleph.bearer import get_bearer_authenticator
from aleph.config import settings
from aleph.models import ApiKey
from aleph.services import generation as gen_module
from tests._bearer_fakes import USERINFO, fake_authenticator, mint

from .conftest import CollectingSpawn, stub_resolver

if TYPE_CHECKING:
    from fastapi import FastAPI

ORIGIN = {"Origin": "http://testserver"}
KEYS = "/api/v1/api-keys"
OWNER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="owner-subject",
    username="owner",
    display_name="Owner User",
    email="owner@example.com",
)
OTHER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="other-subject",
    username="other",
    display_name="Other User",
    email="other@example.com",
)


class StubOAuthClient:
    async def authorize_access_token(self, _request: object) -> dict[str, str]:
        return {"access_token": "stub"}


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def spawn(monkeypatch: pytest.MonkeyPatch) -> CollectingSpawn:
    collector = CollectingSpawn()
    monkeypatch.setattr(
        gen_module.generation_orchestrator, "_resolve_model", stub_resolver()
    )
    monkeypatch.setattr(gen_module.generation_orchestrator, "_spawn", collector)
    return collector


@pytest.fixture
def mcp_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "feature_flag_defaults", "mcp:on")


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _sign_in(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, identity: AuthIdentity
) -> None:
    monkeypatch.setattr(
        "aleph.routers.auth.oauth.create_client", lambda _p: StubOAuthClient()
    )
    monkeypatch.setattr("aleph.routers.auth.fetch_identity", lambda *_a: identity)
    resp = await client.get("/auth/callback", follow_redirects=False)
    assert resp.status_code == 303


async def _create(client: AsyncClient, name: str = "Claude") -> dict[str, Any]:
    resp = await client.post(KEYS, json={"name": name}, headers=ORIGIN)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _mcp(app: FastAPI, key: str, method: str, **params: Any):
    # A fresh client with no cookie jar: an MCP client has only the key.
    async with _client(app) as mcp:
        return await mcp.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers={"Authorization": f"Bearer {key}"},
        )


@pytest.mark.anyio
async def test_a_new_key_is_shown_once_and_stored_only_as_a_digest(
    app: FastAPI, mcp_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        created = await _create(client, "  Claude desktop  ")
        listed = await client.get(KEYS)

    key = created["key"]
    assert len(key) == 43  # secrets.token_urlsafe(32), as in accanto
    assert "." not in key
    assert created["name"] == "Claude desktop"
    [row] = listed.json()["keys"]
    assert row["id"] == created["id"]
    assert set(row) == {"id", "name", "created_at", "revoked_at"}
    assert key not in listed.text
    async with db.async_session() as session:
        [stored] = await session.scalars(select(ApiKey))
    assert stored.digest == hashlib.sha256(key.encode()).hexdigest()


@pytest.mark.anyio
async def test_a_key_drives_mcp_tools_but_not_the_api_directly(
    app: FastAPI, mcp_on: None, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        key = (await _create(client))["key"]

        created = await _mcp(
            app,
            key,
            "tools/call",
            name="create_path",
            arguments={"topic": "Rust ownership", "level": "new_to_it"},
        )
        await spawn.drain()
        listed = await _mcp(app, key, "tools/call", name="list_paths", arguments={})
        async with _client(app) as direct:
            api = await direct.get(
                "/api/v1/paths", headers={"Authorization": f"Bearer {key}"}
            )
            keys = await direct.get(KEYS, headers={"Authorization": f"Bearer {key}"})
        # The learner's own web session sees the path the MCP client made.
        web = (await client.get("/api/v1/paths")).json()["paths"]

    path_id = created.json()["result"]["structuredContent"]["id"]
    [row] = listed.json()["result"]["structuredContent"]["paths"]
    assert row["id"] == path_id
    assert [path["id"] for path in web] == [path_id]
    assert api.status_code == 401
    assert keys.status_code == 403


@pytest.mark.anyio
async def test_revoking_stops_a_key_keeps_its_record_and_needs_the_owner(
    app: FastAPI, mcp_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as owner, _client(app) as other:
        await _sign_in(owner, monkeypatch, OWNER)
        created = await _create(owner)
        key, key_id = created["key"], created["id"]
        assert (await _mcp(app, key, "ping")).status_code == 200

        await _sign_in(other, monkeypatch, OTHER)
        foreign = await other.delete(f"{KEYS}/{key_id}", headers=ORIGIN)
        still_works = await _mcp(app, key, "ping")
        revoked = await owner.delete(f"{KEYS}/{key_id}", headers=ORIGIN)
        first_stamp = (await owner.get(KEYS)).json()["keys"][0]["revoked_at"]
        again = await owner.delete(f"{KEYS}/{key_id}", headers=ORIGIN)
        listed = (await owner.get(KEYS)).json()["keys"]
        after = await _mcp(app, key, "ping")
        unknown = await _mcp(app, "not-a-real-key", "ping")

    assert foreign.status_code == 404
    assert still_works.status_code == 200
    assert (revoked.status_code, again.status_code) == (204, 204)
    assert first_stamp is not None
    assert [(row["id"], row["revoked_at"]) for row in listed] == [(key_id, first_stamp)]
    assert after.status_code == 401
    assert unknown.status_code == 401


@pytest.mark.anyio
async def test_key_routes_need_a_browser_session_the_flag_and_same_origin(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        assert (await client.get(KEYS)).status_code == 401
        await _sign_in(client, monkeypatch, OWNER)
        assert (await client.get(KEYS)).status_code == 404

        monkeypatch.setattr(settings, "feature_flag_defaults", "mcp:on")
        cross = await client.post(
            KEYS, json={"name": "x"}, headers={"Origin": "https://evil.example"}
        )
        blank = await client.post(KEYS, json={"name": "   "}, headers=ORIGIN)
        # A valid OIDC access token is still not a browser session.
        authenticator = fake_authenticator(USERINFO)
        app.dependency_overrides[get_bearer_authenticator] = lambda: authenticator
        async with _client(app) as script:
            oidc = await script.post(
                KEYS,
                json={"name": "x"},
                headers={"Authorization": f"Bearer {mint()}", **ORIGIN},
            )

    assert cross.status_code == 403
    assert blank.status_code == 422
    assert oidc.status_code == 403
    assert oidc.json()["error"]["details"] == {"reason": "browser_session_required"}


@pytest.mark.anyio
async def test_mcp_flag_off_turns_a_valid_key_away(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "feature_flag_defaults", "mcp:on")
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        key = (await _create(client))["key"]
    monkeypatch.setattr(settings, "feature_flag_defaults", "mcp:off")

    assert (await _mcp(app, key, "ping")).status_code == 404
