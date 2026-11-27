"""The content MCP server: read and write a learner's paths from an AI client.

``POST /mcp`` speaks the Model Context Protocol's **Streamable HTTP** transport,
stateless and JSON-only: each JSON-RPC request gets one ``application/json``
reply, there is no ``Mcp-Session-Id`` and no server-sent stream (``GET`` is
``405``, which the transport allows). See ``docs/mcp.md``.

**Every tool is an** ``/api/v1`` **call.** A tool forwards to the existing route
in-process (``httpx.ASGITransport`` over this same app) with the caller's own
``Authorization`` header, so ownership (``404`` for another learner's path),
rate limits, validation, the poll-as-trigger resume and the Quick check reveal
boundary are the web client's, enforced once (Constitution §1, §8). This module
owns only protocol translation and the Markdown export layout.

**Bearer only.** The token is an **API key** the learner created in
Settings (``services/api_keys.py``) or an OIDC access token for the API
audience. A request without ``Authorization`` is ``401`` and the session
cookie is never consulted, so a browser cannot drive these writes with ambient
credentials. The ``401`` names the OAuth protected-resource metadata (RFC 9728)
that MCP clients use to find the identity provider. The whole surface sits
behind the ``mcp`` feature flag (off -> ``404``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from aleph.bearer import BearerAuthenticator, get_bearer_authenticator
from aleph.config import settings
from aleph.db import get_session
from aleph.dependencies import (
    bearer_token,
    get_current_user,
    resolve_api_key_user,
)
from aleph.domains.visuals import strip_visual_fences
from aleph.services import api_keys
from aleph.services.feature_flags import FeatureFlag, FeatureFlagService

if TYPE_CHECKING:
    from collections.abc import Mapping

router = APIRouter(tags=["mcp"])

SERVER_INFO = {"name": "aleph", "version": "1.0.0"}
# Newest first; an unknown requested version is answered with the newest.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
METADATA_PATH = "/.well-known/oauth-protected-resource"

# JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


def _base_url(request: Request) -> str:
    return (settings.public_base_url or str(request.base_url)).rstrip("/")


async def require_mcp_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    authenticator: Annotated[
        BearerAuthenticator | None, Depends(get_bearer_authenticator)
    ],
) -> None:
    """A valid bearer token whose account has the ``mcp`` flag, else 401/404."""
    if bearer_token(request) is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="bearer token required",
            headers={
                "WWW-Authenticate": (
                    f'Bearer resource_metadata="{_base_url(request)}{METADATA_PATH}"'
                )
            },
        )
    token = bearer_token(request)
    user = (
        await resolve_api_key_user(request, session, token)
        if token is not None and api_keys.is_api_key(token)
        else await get_current_user(request, session, authenticator)
    )
    flags = await FeatureFlagService(session).resolve_for_user(user)
    if not flags.get(FeatureFlag.MCP, False):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


@router.get(METADATA_PATH)
async def protected_resource_metadata(request: Request) -> JSONResponse:
    """RFC 9728 metadata: this server's identity provider. ``404`` when bearer
    auth is off (``OIDC_API_AUDIENCE`` unset), since every token would fail."""
    if not settings.oidc_api_audience:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return JSONResponse(
        {
            "resource": f"{_base_url(request)}/mcp",
            "authorization_servers": [settings.oidc_issuer],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["openid", "profile", "email"],
        }
    )


@router.get("/mcp")
async def mcp_stream() -> Response:
    """No server-initiated stream: the transport allows ``405`` here."""
    return Response(status_code=status.HTTP_405_METHOD_NOT_ALLOWED)


@router.post("/mcp", dependencies=[Depends(require_mcp_user)])
async def mcp(request: Request) -> Response:
    """One JSON-RPC message in, one reply (or ``202`` for a notification) out."""
    version = request.headers.get("mcp-protocol-version")
    if version is not None and version not in PROTOCOL_VERSIONS:
        return _rpc_error(None, INVALID_REQUEST, f"unsupported protocol {version}")
    try:
        message = json.loads(await request.body())
    except ValueError:
        return _rpc_error(None, PARSE_ERROR, "invalid JSON")
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _rpc_error(None, INVALID_REQUEST, "expected one JSON-RPC 2.0 message")
    if "method" not in message:
        # A response to a server request; this server sends none.
        return Response(status_code=status.HTTP_202_ACCEPTED)
    if "id" not in message:
        # Notifications (``notifications/initialized``, cancellations) need no reply.
        return Response(status_code=status.HTTP_202_ACCEPTED)

    rpc_id = message["id"]
    method = message["method"]
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _rpc_error(rpc_id, INVALID_PARAMS, "params must be an object")

    if method == "initialize":
        requested = params.get("protocolVersion")
        return _rpc_result(
            rpc_id,
            {
                "protocolVersion": (
                    requested
                    if requested in PROTOCOL_VERSIONS
                    else PROTOCOL_VERSIONS[0]
                ),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            },
        )
    if method == "ping":
        return _rpc_result(rpc_id, {})
    if method == "tools/list":
        return _rpc_result(rpc_id, {"tools": [tool.listing() for tool in TOOLS]})
    if method == "tools/call":
        tool = TOOLS_BY_NAME.get(params.get("name", ""))
        if tool is None:
            return _rpc_error(
                rpc_id, INVALID_PARAMS, f"unknown tool {params.get('name')}"
            )
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _rpc_error(rpc_id, INVALID_PARAMS, "arguments must be an object")
        return _rpc_result(rpc_id, await _call_tool(request, tool, arguments))
    return _rpc_error(rpc_id, METHOD_NOT_FOUND, f"method {method} not found")


def _rpc_result(rpc_id: Any, result: Mapping[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})


def _rpc_error(rpc_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}},
        status_code=(
            status.HTTP_400_BAD_REQUEST if rpc_id is None else status.HTTP_200_OK
        ),
    )


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #

INSTRUCTIONS = (
    "Aleph is an AI tutor. A path is a learning journey for one topic: units of "
    "lessons, each a Read passage and a Quick check. Paths are generated: "
    "create_path starts generation and returns an id; poll get_path until status "
    "is ready, failed or refused. Lessons are generated a few at a time as the "
    "learner works through them, so export_path includes only generated lessons. "
    "A Quick check's answer appears only after the learner has answered it."
)

_UUID = {"type": "string", "format": "uuid"}
_LEVEL = {"type": "string", "enum": ["new_to_it", "some_experience", "work_in_it"]}


class ToolError(Exception):
    """A tool's own failure, reported to the model as ``isError`` content."""


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    properties: Mapping[str, Any]
    required: tuple[str, ...] = ()
    read_only: bool = True
    # Which ``/api/v1`` route serves it: method, path template, body keys.
    method: str = "GET"
    route: str = ""
    body: tuple[str, ...] = ()

    def listing(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": dict(self.properties),
                "required": list(self.required),
                "additionalProperties": False,
            },
            "annotations": {
                "readOnlyHint": self.read_only,
                "destructiveHint": False,
                "openWorldHint": False,
            },
        }


TOOLS = (
    Tool(
        name="list_paths",
        title="List paths",
        description=(
            "The learner's paths, most recently worked first, with status, "
            "progress and the next lesson to do."
        ),
        properties={},
        route="/api/v1/paths",
    ),
    Tool(
        name="get_path",
        title="Get path",
        description=(
            "One path's outline: units and lessons with each lesson's generation "
            "and unlock state. Use it to poll a new path until status resolves."
        ),
        properties={"path_id": _UUID},
        required=("path_id",),
        route="/api/v1/paths/{path_id}",
    ),
    Tool(
        name="get_lesson",
        title="Get lesson",
        description=(
            "One lesson: its Read passage (Markdown), Quick check and, once the "
            "learner has answered, their Attempt with the answer and explanation."
        ),
        properties={"lesson_id": _UUID},
        required=("lesson_id",),
        route="/api/v1/lessons/{lesson_id}",
    ),
    Tool(
        name="export_path",
        title="Export path as Markdown",
        description=(
            "A path's generated content as one Markdown document: every generated "
            "lesson's Read passage and Quick check, with answers only for Quick "
            "checks the learner has answered."
        ),
        properties={"path_id": _UUID},
        required=("path_id",),
    ),
    Tool(
        name="create_path",
        title="Create path",
        description=(
            "Start a new path. Generation runs in the background: this returns the "
            "path id at once; poll get_path until status is ready. Counts against "
            "the learner's daily path limit."
        ),
        properties={
            "topic": {"type": "string", "minLength": 1, "maxLength": 500},
            "level": _LEVEL,
            "guidance": {
                "type": "string",
                "maxLength": 4000,
                "description": "Optional free text steering the outline's shape.",
            },
            "teaching_style": {
                "type": "string",
                "maxLength": 2000,
                "description": "Optional: how lessons should teach.",
            },
            "search_outline": {"type": "boolean", "default": False},
            "search_lessons": {"type": "boolean", "default": False},
        },
        required=("topic", "level"),
        read_only=False,
        method="POST",
        route="/api/v1/paths",
        body=(
            "topic",
            "level",
            "guidance",
            "teaching_style",
            "search_outline",
            "search_lessons",
        ),
    ),
    Tool(
        name="rename_path",
        title="Rename path",
        description="Change a path's display title. Nothing is regenerated.",
        properties={
            "path_id": _UUID,
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
        },
        required=("path_id", "title"),
        read_only=False,
        method="PATCH",
        route="/api/v1/paths/{path_id}",
        body=("title",),
    ),
    Tool(
        name="set_teaching_style",
        title="Set Teaching style",
        description=(
            "Replace a path's Teaching style, or clear it with null. Applies to "
            "lessons generated from now on; written lessons keep theirs."
        ),
        properties={
            "path_id": _UUID,
            "teaching_style": {"type": ["string", "null"], "maxLength": 2000},
        },
        required=("path_id", "teaching_style"),
        read_only=False,
        method="PUT",
        route="/api/v1/paths/{path_id}/teaching-style",
        body=("teaching_style",),
    ),
    Tool(
        name="get_about_you",
        title="Get About you",
        description="What the learner has told Aleph about themselves, or null.",
        properties={},
        route="/api/v1/about-you",
    ),
    Tool(
        name="set_about_you",
        title="Set About you",
        description=(
            "Replace the learner's About you, or clear it with null. Read by "
            "every later generation; nothing already written changes."
        ),
        properties={"about_you": {"type": ["string", "null"], "maxLength": 2000}},
        required=("about_you",),
        read_only=False,
        method="PUT",
        route="/api/v1/about-you",
        body=("about_you",),
    ),
)
TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


async def _call_tool(
    request: Request, tool: Tool, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    missing = [key for key in tool.required if key not in arguments]
    try:
        if missing:
            raise ToolError(f"missing arguments: {', '.join(missing)}")
        # Lets the forwarded /api/v1 calls accept an API key (and only these).
        reset = api_keys.forwarding.set(True)
        try:
            return await _forward(request, tool, arguments)
        finally:
            api_keys.forwarding.reset(reset)
    except ToolError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}


async def _forward(
    request: Request, tool: Tool, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    async with _api_client(request) as api:
        if tool.name == "export_path":
            text = await _export_path(api, _uuid_arg(arguments, "path_id"))
            return {"content": [{"type": "text", "text": text}]}
        route = tool.route.format(
            **{
                key: _uuid_arg(arguments, key)
                for key in tool.required
                if key.endswith("_id")
            }
        )
        body = {key: arguments[key] for key in tool.body if key in arguments}
        data = await _api(api, tool.method, route, body if tool.body else None)
    return {
        "content": [{"type": "text", "text": json.dumps(data)}],
        "structuredContent": data,
    }


def _uuid_arg(arguments: Mapping[str, Any], key: str) -> str:
    try:
        return str(UUID(str(arguments[key])))
    except ValueError as exc:
        raise ToolError(f"{key} must be a UUID") from exc


def _api_client(request: Request) -> AsyncClient:
    """This app, in-process, as the caller: same bearer token, no cookies."""
    return AsyncClient(
        transport=ASGITransport(app=request.app),
        base_url=str(request.base_url),
        headers={"Authorization": request.headers["authorization"]},
    )


async def _api(
    api: AsyncClient, method: str, route: str, body: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    response = await api.request(method, route, json=body)
    data = response.json() if response.content else {}
    if response.is_error:
        error = data.get("error", {}) if isinstance(data, dict) else {}
        raise ToolError(
            f"{response.status_code} {error.get('code', 'error')}: "
            f"{error.get('message', response.text)}"
        )
    return data


async def _export_path(api: AsyncClient, path_id: str) -> str:
    path = await _api(api, "GET", f"/api/v1/paths/{path_id}")
    lessons = {}
    for unit in path["units"]:
        for lesson in unit["lessons"]:
            if lesson["generation_state"] == "generated":
                lessons[lesson["id"]] = await _api(
                    api, "GET", f"/api/v1/lessons/{lesson['id']}"
                )
    return path_markdown(path, lessons)


def path_markdown(path: Mapping[str, Any], lessons: Mapping[str, Any]) -> str:
    """Lay out a path and its generated lessons as one Markdown document.

    ``path`` is a ``GET /paths/{id}`` body and ``lessons`` maps lesson ids to
    ``GET /lessons/{id}`` bodies. Only fields named here are written, so a field
    the lesson API adds later is never exported by accident. Interactive Visuals
    are not exported (their source is sandboxed HTML); their alt text is.
    """
    out = [
        f"# {path['title']}",
        "",
        f"Topic: {path['topic']}",
        f"Level: {path['level']}",
    ]
    if path.get("teaching_style"):
        out.append(f"Teaching style: {path['teaching_style']}")
    for unit in path["units"]:
        out += ["", f"## {unit['position']}. {unit['title']}"]
        if unit.get("summary"):
            out += ["", unit["summary"]]
        for summary in unit["lessons"]:
            out += ["", f"### {summary['position_in_path']}. {summary['title']}"]
            lesson = lessons.get(summary["id"])
            if lesson is None or lesson.get("read_passage") is None:
                out += ["", "_Not generated yet._"]
                continue
            out += ["", strip_visual_fences(lesson["read_passage"]).strip()]
            for visual in lesson.get("visuals") or []:
                out += ["", f"> Interactive Visual, {visual['title']}: {visual['alt']}"]
            check = lesson.get("quick_check")
            if check is None:
                continue
            attempt = lesson.get("attempt")
            out += ["", "**Quick check:** " + check["stem"], ""]
            for index, option in enumerate(check["options"]):
                marks = []
                if attempt is not None and index == attempt["correct_index"]:
                    marks.append("correct")
                if attempt is not None and index == attempt["selected_index"]:
                    marks.append("learner's answer")
                suffix = f" _({', '.join(marks)})_" if marks else ""
                out.append(f"{index + 1}. {option}{suffix}")
            if attempt is not None:
                out += ["", f"_Explanation:_ {attempt['explanation']}"]
    return "\n".join(out) + "\n"
