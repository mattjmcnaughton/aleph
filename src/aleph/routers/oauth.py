"""OAuth 2.1 authorization server endpoints for MCP clients (``docs/mcp.md``).

Discovery is ``/.well-known/oauth-authorization-server`` (RFC 8414). The flow
is Authorization Code + PKCE (S256 only) for public clients identified by a
Client ID Metadata Document, then refresh-token rotation; see
``services/oauth.py``.

``GET /oauth/authorize`` signs the learner in through the existing cookie
flow, validates the request and parks it in the signed session, then sends the
browser to the web client's consent page (``/connect``). That page reads and
answers the parked request through ``/api/v1/oauth/consent`` (browser session
only, same origin, behind the ``mcp`` flag). ``POST /oauth/token`` exchanges a
code or a refresh token.

Errors before the client and its redirect URI are trusted never redirect to
the client (OAuth 2.1 §4.1.2.1): the browser lands on ``/connect?error=…``.
Later errors go back to the client's redirect URI.
"""

from __future__ import annotations

import secrets
from typing import Annotated, Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from aleph.db import get_session
from aleph.dependencies import (
    get_current_user,
    get_optional_current_user,
    require_same_origin,
)
from aleph.models import User  # noqa: TC001
from aleph.routers.auth import OAUTH_PENDING_KEY
from aleph.routers.mcp import base_url
from aleph.routers.v1.api_keys import require_browser_session, require_mcp_enabled
from aleph.services import oauth as service
from aleph.services.feature_flags import FeatureFlag, FeatureFlagService

router = APIRouter(tags=["oauth"])

AUTHORIZE_PATH = "/oauth/authorize"
TOKEN_PATH = "/oauth/token"
CONSENT_PAGE = "/connect"
_CONSENT_KEY = "oauth_consent"
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata(request: Request) -> JSONResponse:
    base = base_url(request)
    return JSONResponse(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}{AUTHORIZE_PATH}",
            "token_endpoint": f"{base}{TOKEN_PATH}",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "client_id_metadata_document_supported": True,
            "scopes_supported": [service.SCOPE],
            "authorization_response_iss_parameter_supported": True,
        }
    )


def _redirect(redirect_uri: str, params: dict[str, str]) -> RedirectResponse:
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        f"{redirect_uri}{separator}{urlencode(params)}", status_code=303
    )


def _consent_error(error: str) -> RedirectResponse:
    return RedirectResponse(f"{CONSENT_PAGE}?error={error}", status_code=303)


def _is_our_resource(resource: str | None, request: Request) -> bool:
    return resource is None or resource.rstrip("/") == f"{base_url(request)}/mcp"


@router.get(AUTHORIZE_PATH)
async def authorize(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User | None, Depends(get_optional_current_user)],
    fetcher: Annotated[service.MetadataFetcher, Depends(service.get_metadata_fetcher)],
) -> Response:
    query = request.query_params
    client_id = query.get("client_id", "")
    redirect_uri = query.get("redirect_uri", "")
    if not service.is_client_id_url(client_id) or not redirect_uri:
        return _consent_error("invalid_client")
    if user is None:
        # Sign in first, so the client's metadata is only fetched for a
        # signed-in learner; the callback comes back here.
        request.session[OAUTH_PENDING_KEY] = str(query)
        return RedirectResponse("/auth/login?oauth=1", status_code=303)
    try:
        client = await service.resolve_client(fetcher, client_id)
    except service.OAuthError:
        return _consent_error("invalid_client")
    if not service.redirect_allowed(client, redirect_uri):
        return _consent_error("invalid_redirect")

    state = query.get("state")
    base = base_url(request)

    def fail(error: str, description: str) -> RedirectResponse:
        params = {"error": error, "error_description": description, "iss": base}
        return _redirect(redirect_uri, params | ({"state": state} if state else {}))

    if query.get("response_type") != "code":
        return fail("unsupported_response_type", "response_type must be code")
    challenge = query.get("code_challenge", "")
    if query.get("code_challenge_method") != "S256" or not 43 <= len(challenge) <= 128:
        return fail("invalid_request", "PKCE with S256 is required")
    if not _is_our_resource(query.get("resource"), request):
        return fail("invalid_target", "unknown resource")
    scope = query.get("scope")
    if scope is not None and set(scope.split()) - {service.SCOPE}:
        return fail("invalid_scope", f"only the {service.SCOPE} scope is supported")
    flags = await FeatureFlagService(session).resolve_for_user(user)
    if not flags.get(FeatureFlag.MCP, False):
        return fail("access_denied", "MCP is not enabled for this account")

    # The validated request waits in the signed session; the consent page
    # answers it by id, so nothing the browser sends can widen it.
    request.session[_CONSENT_KEY] = {
        "id": secrets.token_urlsafe(16),
        "client_id": client.client_id,
        "client_name": client.client_name,
        "loopback_only": client.loopback_only,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "state": state or "",
    }
    return RedirectResponse(CONSENT_PAGE, status_code=303)


# --------------------------------------------------------------------------- #
# Consent API for the web client's /connect page
# --------------------------------------------------------------------------- #

consent_router = APIRouter(
    prefix="/api/v1/oauth/consent",
    tags=["oauth"],
    dependencies=[Depends(require_browser_session), Depends(require_mcp_enabled)],
)


class ConsentRequestDTO(BaseModel):
    id: str
    client_name: str
    client_id: str
    client_host: str
    redirect_host: str
    loopback_only: bool


class ConsentDecision(BaseModel):
    id: str
    approve: bool


class ConsentResultDTO(BaseModel):
    """Where the browser goes next: back to the client."""

    redirect_to: str


def _pending(request: Request) -> dict[str, Any]:
    grant = request.session.get(_CONSENT_KEY)
    if not isinstance(grant, dict):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "no connection request", "reason": "no_request"},
        )
    return grant


@consent_router.get("")
async def get_consent(request: Request, response: Response) -> ConsentRequestDTO:
    response.headers.update(_NO_STORE)
    grant = _pending(request)
    return ConsentRequestDTO(
        id=grant["id"],
        client_name=grant["client_name"],
        client_id=grant["client_id"],
        client_host=str(urlsplit(grant["client_id"]).netloc),
        redirect_host=str(urlsplit(grant["redirect_uri"]).netloc),
        loopback_only=bool(grant.get("loopback_only")),
    )


@consent_router.post("", dependencies=[Depends(require_same_origin)])
async def decide(
    body: ConsentDecision,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConsentResultDTO:
    grant = _pending(request)
    if not secrets.compare_digest(grant["id"], body.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "a newer request replaced this one", "reason": "stale"},
        )
    state = {"state": grant["state"]} if grant["state"] else {}
    base = {"iss": base_url(request)}
    if not body.approve:
        request.session.pop(_CONSENT_KEY, None)
        params = {"error": "access_denied"} | base | state
    else:
        client = service.ClientMetadata(
            client_id=grant["client_id"],
            client_name=grant["client_name"],
            redirect_uris=(grant["redirect_uri"],),
        )
        try:
            code = await service.approve(
                session,
                user_id=user.id,
                client=client,
                redirect_uri=grant["redirect_uri"],
                code_challenge=grant["code_challenge"],
            )
        except service.OAuthError as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=exc.description
            ) from exc
        request.session.pop(_CONSENT_KEY, None)
        params = {"code": code} | base | state
    separator = "&" if "?" in grant["redirect_uri"] else "?"
    return ConsentResultDTO(
        redirect_to=f"{grant['redirect_uri']}{separator}{urlencode(params)}"
    )


# --------------------------------------------------------------------------- #
# Token endpoint
# --------------------------------------------------------------------------- #


def _token_error(error: str, description: str) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status.HTTP_400_BAD_REQUEST,
        headers=_NO_STORE,
    )


@router.post(TOKEN_PATH)
async def token(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> JSONResponse:
    if not request.headers.get("content-type", "").startswith(
        "application/x-www-form-urlencoded"
    ):
        return _token_error("invalid_request", "send a form-encoded body")
    form = dict(parse_qsl((await request.body()).decode("utf-8", "replace")))
    grant_type = form.get("grant_type")
    client_id = form.get("client_id", "")
    if not service.is_client_id_url(client_id):
        return _token_error("invalid_client", "client_id must be its metadata URL")
    if not _is_our_resource(form.get("resource"), request):
        return _token_error("invalid_target", "unknown resource")
    try:
        if grant_type == "authorization_code":
            tokens = await service.exchange_code(
                session,
                code=form.get("code", ""),
                client_id=client_id,
                redirect_uri=form.get("redirect_uri", ""),
                code_verifier=form.get("code_verifier", ""),
            )
        elif grant_type == "refresh_token":
            tokens = await service.refresh(
                session,
                refresh_token=form.get("refresh_token", ""),
                client_id=client_id,
            )
        else:
            return _token_error(
                "unsupported_grant_type", "authorization_code or refresh_token"
            )
    except service.OAuthError as exc:
        return _token_error(exc.error, exc.description)
    return JSONResponse(
        {
            "access_token": tokens.access_token,
            "token_type": "Bearer",
            "expires_in": tokens.expires_in,
            "refresh_token": tokens.refresh_token,
            "scope": service.SCOPE,
        },
        headers=_NO_STORE,
    )
