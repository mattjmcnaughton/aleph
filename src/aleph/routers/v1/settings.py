"""Learner Settings API routes (CONTEXT.md: Settings / Auto-draft).

The learner's own per-account preferences: read them, change some of them.
Session-cookie protected (``get_current_user`` -> ``401`` through the shared
envelope); there is nothing to own by id here, so no ``404`` cases — the
resource is always *this* learner's.

**Not feature-flagged.** Settings are the learner's controls over launched
surfaces, not a surface of their own; the ``flashcards`` flag still gates
whether drafting exists at all, and ``auto_draft_flashcards`` only decides
whether it starts on its own (``routers/v1/flashcards.py``'s trigger route is
the learner's explicit "draft now" either way).

The resolved settings also ride ``GET /api/v1/auth/session`` as
``user.settings`` (the feature-flag delivery precedent, AL-203), so the lesson
view can honour Auto-draft with no second request; ``PATCH`` here is what
changes them, and the SPA folds the response back into that cached session.

Layering (CLAUDE.md): router -> service -> repository. No product event —
changing a setting is not one of PRD §5.7's learner-behaviour signals — so
structlog is the sink, as for the admin flag routes.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import (  # noqa: TC002 - FastAPI resolves annotations.
    AsyncSession,
)

from aleph.db import get_session
from aleph.dependencies import get_current_user
from aleph.dtos.settings import SettingsDTO, SettingsUpdateDTO
from aleph.models import User  # noqa: TC001 - FastAPI resolves annotations.
from aleph.repositories import UserSettingsRepository
from aleph.services.user_settings import load_settings, settings_dto, update_settings

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])
logger = structlog.get_logger()

CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=SettingsDTO)
async def get_settings(user: CurrentUser, session: Session) -> SettingsDTO:
    """The learner's effective settings — every setting, defaults filled in."""
    return settings_dto(await load_settings(UserSettingsRepository(session), user.id))


@router.patch("", response_model=SettingsDTO)
async def patch_settings(
    body: SettingsUpdateDTO, user: CurrentUser, session: Session
) -> SettingsDTO:
    """Change any subset of the settings; the response is the full new state.

    Fields absent from the body are untouched (``exclude_unset`` — a client
    that sends only the switch it flipped never resets another). ``{}`` is a
    read. An unknown field is ``422`` (the DTO forbids extras).
    """
    changes = body.model_dump(exclude_unset=True)
    view = await update_settings(UserSettingsRepository(session), user.id, changes)
    await session.commit()
    if changes:
        logger.info("settings_updated", user_id=str(user.id), **changes)
    return settings_dto(view)
