"""Public profile reads: ``/api/v1/public/*`` (Public profiles TDD §4).

Constitution §1's one bounded exception: anonymous ``GET``s of a projection a
learner chose to publish. This router deliberately has **no auth dependency**:
nothing here reads the session or a bearer token, so a public read never renews
or sets a cookie, and a signed-in visitor gets byte-for-byte what an anonymous
one gets (SR 4). It never writes and never starts generation.

Every absent case — unknown Handle, profile off, flag off for the owner,
unshared, deleted, foreign, Skipped — is the same ``404 not_found`` (D5).
``Cache-Control: no-store`` and ``X-Robots-Tag: noindex`` are added by the
app's cache middleware to every response under this prefix, 404s included.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID  # noqa: TC003 - FastAPI resolves route-param annotations.

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from aleph.db import get_session
from aleph.domains.public_profiles import is_handle
from aleph.dtos.public import (  # noqa: TC001 - FastAPI resolves annotations.
    PublicBeatDTO,
    PublicBriefDTO,
    PublicPathDTO,
    PublicProfileDTO,
)
from aleph.services.public_profiles import PublicProfileService

Session = Annotated[AsyncSession, Depends(get_session)]
router = APIRouter(prefix="/api/v1/public", tags=["public"])


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


def _found[T](value: T | None) -> T:
    if value is None:
        raise _not_found()
    return value


def _handle(handle: str) -> str:
    # Only a well-formed, normalized Handle reaches a query; anything else is
    # absent by definition.
    if not is_handle(handle):
        raise _not_found()
    return handle


Handle = Annotated[str, Depends(_handle)]


@router.get("/profiles/{handle}")
async def get_profile(handle: Handle, session: Session) -> PublicProfileDTO:
    return _found(await PublicProfileService(session).profile(handle))


@router.get("/profiles/{handle}/paths/{path_id}")
async def get_path(handle: Handle, path_id: UUID, session: Session) -> PublicPathDTO:
    return _found(await PublicProfileService(session).path(handle, path_id))


@router.get("/profiles/{handle}/beats/{beat_id}")
async def get_beat(
    handle: Handle,
    beat_id: UUID,
    session: Session,
    cursor: Annotated[int | None, Query(ge=1)] = None,
) -> PublicBeatDTO:
    """One page of Briefs, newest first; ``cursor`` is the last page's
    ``next_cursor`` (a Brief number)."""
    return _found(
        await PublicProfileService(session).beat(handle, beat_id, cursor=cursor)
    )


@router.get("/profiles/{handle}/beats/{beat_id}/briefs/{brief_id}")
async def get_brief(
    handle: Handle, beat_id: UUID, brief_id: UUID, session: Session
) -> PublicBriefDTO:
    return _found(await PublicProfileService(session).brief(handle, beat_id, brief_id))
