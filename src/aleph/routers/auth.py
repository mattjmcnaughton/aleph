"""Authentication routes: OIDC login / callback / logout + session (AL-020/021).

Ported near-verbatim from habagou, trimmed of its workflow-event emission. The
``GET /api/v1/auth/session`` probe with derived-admin classification is AL-021
(TDD §7/D14).
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import parse_qsl, urlencode

import structlog
from authlib.integrations.base_client.errors import OAuthError
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import (  # noqa: TC002 - FastAPI resolves annotations.
    AsyncSession,
)

from aleph import events
from aleph.auth import fetch_identity, oauth
from aleph.authz import is_admin
from aleph.config import settings
from aleph.db import get_session
from aleph.dependencies import get_optional_current_user
from aleph.domains.public_profiles import (
    CopyIntent,
    copy_destination,
    parse_copy_intent,
)
from aleph.dtos.auth import SessionDTO, UserDTO
from aleph.models import User  # noqa: TC001 - FastAPI resolves annotations.
from aleph.services.auth import AuthService
from aleph.services.feature_flags import FeatureFlagService

router = APIRouter(tags=["auth"])
logger = structlog.get_logger()


# Session key for the typed sign-in copy intent (Public profiles TDD §5, SR 6).
COPY_INTENT_KEY = "copy_intent"
# Session key for an MCP client's authorization request awaiting sign-in
# (``routers/oauth.py``); the callback returns to ``/oauth/authorize``.
OAUTH_PENDING_KEY = "oauth_pending"


@router.get("/auth/login")
async def login(request: Request, copy: str | None = None) -> Response:
    """Start the OIDC flow; ``copy=path:<handle>:<uuid>`` (or ``beat:``) returns
    the visitor to the prefilled form after sign-in.

    There is no free-form ``next``. The value is parsed against that grammar and
    stored as ``{kind, handle, id}`` beside Authlib's state; anything else is
    ignored, and a stale intent from an earlier attempt is dropped.
    """
    client = oauth.create_client(settings.oidc_provider)
    if client is None:
        raise RuntimeError(f"auth provider is not registered: {settings.oidc_provider}")
    # Only ``/oauth/authorize`` signs in with ``oauth=1``; any other sign-in
    # drops an abandoned authorization request.
    if request.query_params.get("oauth") != "1":
        request.session.pop(OAUTH_PENDING_KEY, None)
    intent = parse_copy_intent(copy)
    if intent is None:
        request.session.pop(COPY_INTENT_KEY, None)
    else:
        request.session[COPY_INTENT_KEY] = {
            "kind": intent.kind,
            "handle": intent.handle,
            "id": str(intent.item_id),
        }
    callback_url = str(request.url_for("auth_callback"))
    return await client.authorize_redirect(request, callback_url)


def _pop_copy_destination(request: Request) -> str | None:
    """Consume the stored intent (single-use) and build its destination."""
    stored = request.session.pop(COPY_INTENT_KEY, None)
    if not isinstance(stored, dict):
        return None
    # Re-parse what was stored: the session is signed, but the grammar is the
    # only thing allowed to produce a destination.
    intent: CopyIntent | None = parse_copy_intent(
        f"{stored.get('kind')}:{stored.get('handle')}:{stored.get('id')}"
    )
    return copy_destination(intent) if intent is not None else None


def _pop_oauth_destination(request: Request) -> str | None:
    """Consume a stored authorization request; the path is fixed, only the
    re-encoded query comes from the session."""
    stored = request.session.pop(OAUTH_PENDING_KEY, None)
    if not isinstance(stored, str):
        return None
    return "/oauth/authorize?" + urlencode(parse_qsl(stored))


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
        # The cookie sign-in is the web client's: a new account's
        # ``account_created`` carries ``client=web`` (TDD §12).
        events.request_client.set(events.CLIENT_WEB)
        user = await AuthService(session).sign_in(identity)
    except (OAuthError, ValueError) as exc:
        logger.warning("auth_callback_failed", error=str(exc))
        request.session.pop(COPY_INTENT_KEY, None)
        request.session.pop(OAUTH_PENDING_KEY, None)
        return RedirectResponse(url="/login?error=auth_failed", status_code=303)

    destination = (
        _pop_copy_destination(request) or _pop_oauth_destination(request) or "/"
    )
    request.session["user_id"] = str(user.id)
    return RedirectResponse(url=destination, status_code=303)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request) -> Response:
    request.session.clear()
    response = Response(status_code=204)
    response.delete_cookie("session", path="/")
    return response


@router.get("/api/v1/auth/session", response_model=SessionDTO)
async def get_auth_session(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User | None, Depends(get_optional_current_user)],
) -> SessionDTO:
    """Current session for the SPA root ``beforeLoad`` (TDD §6/§7/D14).

    Works for anonymous requests (``authenticated: false``) — the frontend calls
    it signed-out to decide whether to render the login gate, so it must never
    ``401``. Admins receive the model-picker allowlist; everyone else gets ``[]``.

    This is also the **only** feature-flag delivery surface (AL-203): the
    learner's resolved map rides the payload the SPA already fetches on every
    load, so ``useFeatureFlag`` can gate a surface with no second request and
    regular learners never touch the admin flag routes.

    A native client calls it with its bearer token: the same payload for the
    same account, and an invalid token is ``401`` (it never reads as signed out).
    """
    # Carries email and flags: never stored by any cache (Public profiles §4).
    response.headers["Cache-Control"] = "private, no-store"
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
            feature_flags=await FeatureFlagService(session).resolve_for_user(user),
        ),
    )
