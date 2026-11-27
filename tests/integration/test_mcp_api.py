"""The content MCP server (``/mcp``) over real Postgres (docs/mcp.md).

Bearer tokens are minted locally and verified by the real ``BearerAuthenticator``
over the in-memory JWKS fake; generation uses the deterministic stub model and
a drainable spawn, as in ``test_lessons_api``. Each tool forwards to an
``/api/v1`` route, so these tests check what crosses the protocol boundary:
auth, the flag gate, ownership, and the Quick check reveal boundary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from httpx import ASGITransport, AsyncClient

from aleph import db
from aleph.app import create_app
from aleph.bearer import get_bearer_authenticator
from aleph.config import settings
from aleph.models import QuickCheck
from aleph.services import generation as gen_module
from tests._bearer_fakes import USERINFO, fake_authenticator, mint

from .conftest import CollectingSpawn, stub_resolver

if TYPE_CHECKING:
    from fastapi import FastAPI

OTHER_USERINFO = {**USERINFO, "sub": "subject-2", "email": "other@example.com"}


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


def _as(app: FastAPI, userinfo: dict[str, Any]) -> None:
    authenticator = fake_authenticator(userinfo)
    app.dependency_overrides[get_bearer_authenticator] = lambda: authenticator


def _auth(subject: str = "subject-1") -> dict[str, str]:
    return {"Authorization": f"Bearer {mint(sub=subject)}"}


async def _rpc(
    client: AsyncClient,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    subject: str = "subject-1",
) -> dict[str, Any]:
    resp = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers=_auth(subject),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _call(
    client: AsyncClient, name: str, subject: str = "subject-1", **arguments: Any
) -> dict[str, Any]:
    body = await _rpc(
        client, "tools/call", {"name": name, "arguments": arguments}, subject=subject
    )
    return body["result"]


async def _ready_path(client: AsyncClient, spawn: CollectingSpawn) -> dict[str, Any]:
    created = await _call(
        client, "create_path", topic="Rust ownership", level="new_to_it"
    )
    assert "isError" not in created, created
    path_id = created["structuredContent"]["id"]
    await spawn.drain()
    await _call(client, "get_path", path_id=path_id)
    await spawn.drain()
    path = (await _call(client, "get_path", path_id=path_id))["structuredContent"]
    assert path["status"] == "ready"
    return path


@pytest.mark.anyio
async def test_requests_without_a_bearer_token_are_401_with_resource_metadata(
    app: FastAPI, mcp_on: None
) -> None:
    async with _client(app) as client:
        resp = await client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}
        )
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == (
        'Bearer resource_metadata="http://testserver'
        '/.well-known/oauth-protected-resource"'
    )


@pytest.mark.anyio
async def test_the_mcp_flag_off_hides_the_server(app: FastAPI) -> None:
    _as(app, USERINFO)
    async with _client(app) as client:
        resp = await client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=_auth()
        )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_initialize_negotiates_and_lists_tools(
    app: FastAPI, mcp_on: None
) -> None:
    _as(app, USERINFO)
    async with _client(app) as client:
        init = await _rpc(client, "initialize", {"protocolVersion": "2025-06-18"})
        old = await _rpc(client, "initialize", {"protocolVersion": "1999-01-01"})
        notified = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=_auth(),
        )
        tools = await _rpc(client, "tools/list")
        unknown = await _rpc(client, "nope")

    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert old["result"]["protocolVersion"] == "2025-11-25"
    assert notified.status_code == 202
    names = {tool["name"] for tool in tools["result"]["tools"]}
    assert names == {
        "list_paths",
        "get_path",
        "get_lesson",
        "export_path",
        "create_path",
        "rename_path",
        "set_teaching_style",
        "get_about_you",
        "set_about_you",
    }
    assert unknown["error"]["code"] == -32601


@pytest.mark.anyio
async def test_create_rename_and_read_a_path(
    app: FastAPI, mcp_on: None, spawn: CollectingSpawn
) -> None:
    _as(app, USERINFO)
    async with _client(app) as client:
        path = await _ready_path(client, spawn)
        renamed = await _call(
            client, "rename_path", path_id=path["id"], title="Borrowing"
        )
        styled = await _call(
            client,
            "set_teaching_style",
            path_id=path["id"],
            teaching_style="Use analogies",
        )
        listed = await _call(client, "list_paths")
        await _call(client, "set_about_you", about_you="I write Go at work")
        about = await _call(client, "get_about_you")

    assert renamed["structuredContent"]["title"] == "Borrowing"
    assert renamed["structuredContent"]["topic"] == "Rust ownership"
    assert styled["structuredContent"]["teaching_style"] == "Use analogies"
    [row] = listed["structuredContent"]["paths"]
    assert (row["id"], row["title"]) == (path["id"], "Borrowing")
    assert about["structuredContent"] == {"about_you": "I write Go at work"}


@pytest.mark.anyio
async def test_api_errors_come_back_as_tool_errors(
    app: FastAPI, mcp_on: None, spawn: CollectingSpawn
) -> None:
    _as(app, USERINFO)
    async with _client(app) as client:
        path = await _ready_path(client, spawn)
        invalid = await _call(client, "create_path", topic="", level="new_to_it")
        missing = await _call(client, "rename_path", path_id=path["id"])
        not_uuid = await _call(client, "get_path", path_id="abc")
        # Another learner's path is 404, exactly as on /api/v1.
        _as(app, OTHER_USERINFO)
        foreign = await _call(
            client, "get_path", subject="subject-2", path_id=path["id"]
        )
        foreign_export = await _call(
            client, "export_path", subject="subject-2", path_id=path["id"]
        )

    assert invalid["isError"] is True
    assert invalid["content"][0]["text"].startswith("422 validation_error")
    assert missing == {
        "content": [{"type": "text", "text": "missing arguments: title"}],
        "isError": True,
    }
    assert not_uuid["isError"] is True
    assert foreign["content"][0]["text"].startswith("404 not_found")
    assert foreign_export["isError"] is True


@pytest.mark.anyio
@pytest.mark.workflow("W6")
async def test_reads_and_exports_hide_the_answer_until_the_learner_attempts(
    app: FastAPI, mcp_on: None, spawn: CollectingSpawn
) -> None:
    _as(app, USERINFO)
    async with _client(app) as client:
        path = await _ready_path(client, spawn)
        first = path["units"][0]["lessons"][0]
        before = await _call(client, "get_lesson", lesson_id=first["id"])
        export_before = await _call(client, "export_path", path_id=path["id"])
        async with db.async_session() as session:
            from sqlalchemy import select

            check = await session.scalar(
                select(QuickCheck).where(QuickCheck.lesson_id == first["id"])
            )
            assert check is not None
        wrong = (check.correct_index + 1) % len(check.options)
        attempt = await client.post(
            f"/api/v1/lessons/{first['id']}/attempt",
            json={"selected_index": wrong},
            headers=_auth(),
        )
        assert attempt.status_code == 200, attempt.text
        export_after = await _call(client, "export_path", path_id=path["id"])

    lesson = before["structuredContent"]
    assert lesson["read_passage"]
    assert lesson["attempt"] is None
    assert "correct_index" not in before["content"][0]["text"]

    text_before = export_before["content"][0]["text"]
    assert text_before.startswith(f"# {path['title']}\n")
    assert check.stem in text_before
    assert "_(correct" not in text_before
    assert check.explanation not in text_before

    text_after = export_after["content"][0]["text"]
    assert (
        f"{check.correct_index + 1}. {check.options[check.correct_index]} _(correct)_"
        in (text_after)
    )
    assert f"{wrong + 1}. {check.options[wrong]} _(learner's answer)_" in text_after
    assert f"_Explanation:_ {check.explanation}" in text_after
