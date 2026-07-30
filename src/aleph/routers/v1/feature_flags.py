"""Admin feature-flag API routes (AL-203, epic #82 owner amendment 1).

Flags are defined in code (:mod:`aleph.services.feature_flags`); these endpoints
manage the per-user database overrides that are the only *exceptions* to a
flag's default. The whole surface is admin-only: regular learners receive their
resolved flag map on the auth session probe (``GET /api/v1/auth/session``) and
never talk to these routes.

Admin status is derived from the email domain at request time
(:func:`aleph.authz.is_admin`), so the gate follows the identity provider with
no stored role to drift. Non-admin is ``403`` rather than ``404`` here on
purpose: unlike a learner's own resources (TDD §6, where existence is not
disclosed), this is a fixed operator surface with nothing to leak.

Layering (CLAUDE.md): router → service → repository. No product events this
phase — admin flag ops are operator actions, not learner behaviour, so structlog
is the right sink (PRD §5.7's event set is untouched).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID  # noqa: TC003 - FastAPI resolves route-param annotations.

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import (  # noqa: TC002 - FastAPI resolves annotations.
    AsyncSession,
)

from aleph.authz import is_admin
from aleph.config import settings
from aleph.db import get_session
from aleph.dependencies import get_current_user
from aleph.dtos.feature_flags import (
    FeatureFlagListDTO,
    FeatureFlagOverrideDTO,
    FeatureFlagOverrideSetDTO,
)
from aleph.models import User  # noqa: TC001 - FastAPI resolves annotations.
from aleph.services.feature_flags import FeatureFlagService, known_flag_keys

router = APIRouter(prefix="/api/v1/admin/feature-flags", tags=["feature-flags"])
logger = structlog.get_logger()

Session = Annotated[AsyncSession, Depends(get_session)]


async def require_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Reject a signed-in non-admin with ``403`` (signed-out is already ``401``)."""
    if not is_admin(current_user, settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="feature-flag management requires an admin account",
        )
    return current_user


Admin = Annotated[User, Depends(require_admin)]


@router.get("", response_model=FeatureFlagListDTO)
async def list_feature_flags(session: Session, _admin: Admin) -> FeatureFlagListDTO:
    """Every registered flag with its effective default and override count."""
    return FeatureFlagListDTO(flags=await FeatureFlagService(session).list_flags())


@router.put(
    "/{flag_key}/users/{user_id}",
    response_model=FeatureFlagOverrideDTO,
    responses={404: {"description": "Unknown flag or user"}},
)
async def set_feature_flag_override(
    flag_key: str,
    user_id: UUID,
    override: FeatureFlagOverrideSetDTO,
    session: Session,
    admin: Admin,
) -> FeatureFlagOverrideDTO:
    """Force one flag on or off for one learner (upsert; a repeat updates)."""
    _ensure_known_flag(flag_key)
    updated = await FeatureFlagService(session).set_user_override(
        flag_key=flag_key, user_id=user_id, enabled=override.enabled
    )
    if not updated:
        raise _user_not_found(user_id)
    logger.info(
        "feature_flag_override_set",
        flag_key=flag_key,
        target_user_id=str(user_id),
        enabled=override.enabled,
        admin_user_id=str(admin.id),
    )
    return FeatureFlagOverrideDTO(
        flag_key=flag_key, user_id=user_id, enabled=override.enabled
    )


@router.delete(
    "/{flag_key}/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"description": "Unknown flag"}},
)
async def clear_feature_flag_override(
    flag_key: str,
    user_id: UUID,
    session: Session,
    admin: Admin,
) -> None:
    """Drop one learner's override so the flag's default applies again."""
    _ensure_known_flag(flag_key)
    deleted = await FeatureFlagService(session).clear_user_override(
        flag_key=flag_key, user_id=user_id
    )
    # Idempotent: clearing an absent override is a no-op 204, not a 404. "Put
    # this learner back on the default" is already true, and an operator
    # retrying a failed request must not be told they broke something.
    logger.info(
        "feature_flag_override_cleared",
        flag_key=flag_key,
        target_user_id=str(user_id),
        deleted=deleted,
        admin_user_id=str(admin.id),
    )


def _ensure_known_flag(flag_key: str) -> None:
    """``404`` for a key the code registry does not define."""
    if flag_key not in known_flag_keys():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown feature flag: {flag_key}",
        )


def _user_not_found(user_id: UUID) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"user not found: {user_id}",
    )
