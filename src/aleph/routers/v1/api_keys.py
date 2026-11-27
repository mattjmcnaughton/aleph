"""API keys: list, create and revoke the learner's keys (Settings → MCP).

The list also carries **connections**, the apps connected through OAuth
(``services/oauth.py``); ``DELETE`` disconnects one the same way it revokes a
key.

Behind the ``mcp`` flag (off -> ``404``). **Browser session only**, as in
accanto: any ``Authorization`` header is ``403 browser_session_required``, so
no key, script or agent can list, mint or revoke keys. Writes check the origin
like every other cookie write that hands out access.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID  # noqa: TC003 - FastAPI resolves route-param annotations.

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from aleph.db import get_session
from aleph.dependencies import bearer_token, get_current_user, require_same_origin
from aleph.dtos.api_keys import (
    ApiKeyDTO,
    ApiKeyListResponse,
    ConnectionDTO,
    CreateApiKeyRequest,
    CreatedApiKeyResponse,
)
from aleph.models import ApiKey, User  # noqa: TC001 - FastAPI resolves annotations.
from aleph.services import api_keys
from aleph.services.feature_flags import FeatureFlag, FeatureFlagService

CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]


async def require_browser_session(request: Request) -> None:
    if bearer_token(request) is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "sign in to Aleph in the browser for this action",
                "reason": "browser_session_required",
            },
        )


async def require_mcp_enabled(user: CurrentUser, session: Session) -> None:
    flags = await FeatureFlagService(session).resolve_for_user(user)
    if not flags.get(FeatureFlag.MCP, False):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


router = APIRouter(
    prefix="/api/v1/api-keys",
    tags=["mcp"],
    dependencies=[Depends(require_browser_session), Depends(require_mcp_enabled)],
)


def _dto(key: ApiKey) -> ApiKeyDTO:
    return ApiKeyDTO(
        id=key.id, name=key.name, created_at=key.created_at, revoked_at=key.revoked_at
    )


@router.get("")
async def list_keys(user: CurrentUser, session: Session) -> ApiKeyListResponse:
    keys = await api_keys.list_keys(session, user.id)
    connections = await api_keys.list_connections(session, user.id)
    return ApiKeyListResponse(
        keys=[_dto(key) for key in keys],
        connections=[
            ConnectionDTO(
                id=key.id,
                name=key.name,
                client_host=urlsplit(key.client_id or "").netloc,
                created_at=key.created_at,
            )
            for key in connections
        ],
    )


@router.post(
    "", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_same_origin)]
)
async def create_key(
    body: CreateApiKeyRequest, user: CurrentUser, session: Session
) -> CreatedApiKeyResponse:
    key, secret = await api_keys.issue_key(session, user_id=user.id, name=body.name)
    return CreatedApiKeyResponse(id=key.id, name=key.name, key=secret)


@router.delete(
    "/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_same_origin)],
)
async def revoke_key(key_id: UUID, user: CurrentUser, session: Session) -> Response:
    if not await api_keys.revoke_key(session, user_id=user.id, key_id=key_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
