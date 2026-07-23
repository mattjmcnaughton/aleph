"""FastAPI dependencies shared by API routers (AL-020 auth gate).

Ported from habagou, trimmed of its workflow-event emission. ``get_current_user``
is the current-user dependency AL-021 (session/admin) and AL-050/051 (route
protection) consume to gate ``/api/v1/*`` endpoints.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import (  # noqa: TC002 - FastAPI resolves annotations.
    AsyncSession,
)

from aleph.db import get_session
from aleph.models import (  # noqa: TC001 - FastAPI resolves annotations.
    User,
)


async def get_current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Resolve the authenticated user or reject the request with ``401``."""
    user = await get_optional_current_user(request, session)
    if user is not None:
        return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="authentication required",
    )


async def get_optional_current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User | None:
    """Resolve the signed-in user, clearing a stale or malformed session."""
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


def _bind_user_to_request(request: Request, user: User) -> None:
    user_id = str(user.id)
    request.state.current_user_id = user_id
    structlog.contextvars.bind_contextvars(user_id=user_id)
