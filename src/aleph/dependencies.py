"""FastAPI dependencies shared by API routers (AL-020 auth gate).

Ported from habagou, trimmed of its workflow-event emission. ``get_current_user``
is the current-user dependency AL-021 (session/admin) and AL-050/051 (route
protection) consume to gate ``/api/v1/*`` endpoints.

Two credentials resolve to the same local account:

* **Bearer token** (native clients): when an ``Authorization`` header is
  present, it alone decides the request. A valid ``Bearer`` access token
  (``aleph/bearer.py``) resolves the account by ``(issuer, subject)`` through
  the cookie flow's own :class:`~aleph.services.auth.AuthService` mapping; a
  malformed or invalid one is ``401`` — never a fall back to the cookie, so a
  client cannot have a bad token silently papered over by an ambient cookie.
  The session is neither read nor written on this path.
* **Session cookie** (the web SPA): no ``Authorization`` header, the existing
  behaviour, unchanged.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import (  # noqa: TC002 - FastAPI resolves annotations.
    AsyncSession,
)

from aleph.bearer import (
    BearerAuthenticator,
    IdentityProviderUnavailable,
    InvalidBearerToken,
    get_bearer_authenticator,
)
from aleph.db import get_session
from aleph.models import (  # noqa: TC001 - FastAPI resolves annotations.
    User,
)
from aleph.services.auth import AuthService

logger = structlog.get_logger()

# RFC 6750 §3: a rejected bearer credential names the scheme and the error.
_INVALID_TOKEN_HEADERS = {"WWW-Authenticate": 'Bearer error="invalid_token"'}


async def get_current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    authenticator: Annotated[
        BearerAuthenticator | None, Depends(get_bearer_authenticator)
    ],
) -> User:
    """Resolve the authenticated user or reject the request with ``401``."""
    user = await get_optional_current_user(request, session, authenticator)
    if user is not None:
        return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="authentication required",
    )


async def get_optional_current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    authenticator: Annotated[
        BearerAuthenticator | None, Depends(get_bearer_authenticator)
    ],
) -> User | None:
    """Resolve the signed-in user, clearing a stale or malformed session.

    ``None`` means anonymous. A present ``Authorization`` header is never
    anonymous: it either authenticates or raises ``401``.
    """
    token = bearer_token(request)
    if token is not None:
        return await _resolve_bearer_user(request, session, authenticator, token)

    raw_user_id = request.session.get("user_id")
    if not raw_user_id:
        return None

    try:
        user_id = UUID(str(raw_user_id))
    except ValueError:
        request.session.clear()
        return None

    # ``session.get`` (identity-map lookup by PK) is habagou-shape: the cookie
    # carries only the local UUID, so a single primary-key load resolves the
    # user; a row that has since been deleted clears the stale cookie.
    user = await session.get(User, user_id)
    if user is None:
        request.session.clear()
        return None

    _bind_user_to_request(request, user)
    return user


def bearer_token(request: Request) -> str | None:
    """The ``Authorization: Bearer`` token, ``None`` when no header is sent.

    Any ``Authorization`` header that is not exactly one well-formed ``Bearer
    <token>`` (another scheme, an empty token, repeated headers) is ``401``:
    the client clearly meant to authenticate with it, so it must not degrade
    to the cookie or to anonymous.
    """
    values = request.headers.getlist("authorization")
    if not values:
        return None
    if len(values) == 1:
        scheme, _, token = values[0].strip().partition(" ")
        token = token.strip()
        if scheme.lower() == "bearer" and token and " " not in token:
            return token
    logger.info("bearer_auth_rejected", reason="malformed authorization header")
    raise _invalid_token()


async def _resolve_bearer_user(
    request: Request,
    session: AsyncSession,
    authenticator: BearerAuthenticator | None,
    token: str,
) -> User:
    if authenticator is None:
        logger.info("bearer_auth_rejected", reason="bearer auth not configured")
        raise _invalid_token()
    try:
        identity = await authenticator.authenticate(token)
    except InvalidBearerToken as exc:
        logger.info("bearer_auth_rejected", reason=str(exc))
        raise _invalid_token() from exc
    except IdentityProviderUnavailable as exc:
        logger.warning("bearer_auth_provider_unavailable", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="identity provider unavailable",
        ) from exc

    # The cookie flow's own identity -> account mapping (provision on first
    # sight, refresh presentation claims), so a learner reaches the same
    # account from the web and from the app.
    user = await AuthService(session).resolve(identity)
    _bind_user_to_request(request, user, auth_method="bearer")
    return user


def _invalid_token() -> HTTPException:
    # One generic message for every failure: the client learns nothing about
    # which check rejected the token (the reason is logged server-side).
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid bearer token",
        headers=_INVALID_TOKEN_HEADERS,
    )


def _bind_user_to_request(
    request: Request, user: User, *, auth_method: str = "session"
) -> None:
    user_id = str(user.id)
    request.state.current_user_id = user_id
    request.state.auth_method = auth_method
    structlog.contextvars.bind_contextvars(user_id=user_id, auth_method=auth_method)
