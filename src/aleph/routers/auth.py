"""Authentication routes: OIDC login / callback / logout + session (AL-020/021).

Ported near-verbatim from habagou, trimmed of its workflow-event emission. The
``GET /api/v1/auth/session`` probe with derived-admin classification is AL-021
(TDD §7/D14).
"""

from __future__ import annotations

from typing import Annotated

import structlog
from authlib.integrations.base_client.errors import OAuthError
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import (  # noqa: TC002 - FastAPI resolves annotations.
    AsyncSession,
)

from aleph.auth import fetch_identity, oauth
from aleph.authz import is_admin
from aleph.config import settings
from aleph.db import get_session
from aleph.dependencies import get_optional_current_user
from aleph.dtos.auth import SessionDTO, UserDTO
from aleph.services.auth import AuthService

router = APIRouter(tags=["auth"])
logger = structlog.get_logger()


@router.get("/auth/login")
async def login(request: Request) -> Response:
    client = oauth.create_client(settings.oidc_provider)
    if client is None:
        raise RuntimeError(f"auth provider is not registered: {settings.oidc_provider}")
    callback_url = str(request.url_for("auth_callback"))
    return await client.authorize_redirect(request, callback_url)


@router.get("/auth/callback")
async def auth_callback(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    try:
        client = oauth.create_client(settings.oidc_provider)
        if client is None:
            raise RuntimeError(
                f"auth provider is not registered: {settings.oidc_provider}"
            )
        token = await client.authorize_access_token(request)
        identity = fetch_identity(token)
        user = await AuthService(session).sign_in(identity)
    except (OAuthError, ValueError) as exc:
        logger.warning("auth_callback_failed", error=str(exc))
        return RedirectResponse(url="/login?error=auth_failed", status_code=303)

    request.session["user_id"] = str(user.id)
    return RedirectResponse(url="/", status_code=303)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request) -> Response:
    request.session.clear()
    response = Response(status_code=204)
    response.delete_cookie("session", path="/")
    return response


@router.get("/api/v1/auth/session", response_model=SessionDTO)
async def get_auth_session(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SessionDTO:
    """Current session for the SPA root ``beforeLoad`` (TDD §6/§7/D14).

    Works for anonymous requests (``authenticated: false``) — the frontend calls
    it signed-out to decide whether to render the login gate, so it must never
    ``401``. Admins receive the model-picker allowlist; everyone else gets ``[]``.
    """
    user = await get_optional_current_user(request, session)
    if user is None:
        return SessionDTO(authenticated=False, provider=settings.oidc_provider)

    admin = is_admin(user, settings)
    return SessionDTO(
        authenticated=True,
        provider=settings.oidc_provider,
        user=UserDTO(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            email=user.email,
            is_admin=admin,
            model_allowlist=list(settings.allowlist_ids) if admin else [],
        ),
    )
