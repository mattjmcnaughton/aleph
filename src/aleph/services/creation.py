"""Path and Beat creation transactions, followed by target-owned dispatch.

Inputs have passed DTO and model-picker validation at the boundary. Insertion
uses the request session: no nested creation transaction can escape its commit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aleph import events
from aleph.repositories import BeatRepository, PathRepository
from aleph.services.briefing import briefing_service
from aleph.services.generation import generation_orchestrator
from aleph.services.rate_limit import build_daily_rate_limiter

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.dtos.beats import DeployBeatRequest
    from aleph.dtos.paths import CreatePathRequest
    from aleph.models import Beat, Path, User


async def create_path(
    session: AsyncSession, user: User, body: CreatePathRequest, *, is_admin: bool
) -> Path:
    await build_daily_rate_limiter(session).check_path_creation(
        user_id=user.id, is_admin=is_admin
    )
    path = await PathRepository(session).create(
        user_id=user.id,
        topic=body.topic,
        level=body.level,
        guidance=body.guidance,
        teaching_style=body.teaching_style,
        model_outline=body.model_outline,
        model_lesson=body.model_lesson,
    )
    await session.commit()
    generation_orchestrator.dispatch_created_path(path)
    return path


async def create_beat(
    session: AsyncSession,
    user: User,
    body: DeployBeatRequest,
    *,
    is_admin: bool,
    local_today: date,
) -> Beat:
    await build_daily_rate_limiter(session).check_beat_creation(
        user_id=user.id, is_admin=is_admin
    )
    beat = await BeatRepository(session).create(
        user_id=user.id,
        topic=body.topic,
        level=body.level,
        anchor_weekday=body.anchor_weekday,
        guidance=body.guidance,
        model_research=body.model_research,
        model_brief=body.model_brief,
    )
    await session.commit()
    await briefing_service.trigger_initial(
        beat.id, user_id=user.id, local_today=local_today, is_admin=is_admin
    )
    await session.refresh(beat)
    events.emit_beat_deployed(
        account_id=user.id,
        beat_id=beat.id,
        beat_level=beat.level.value,
        anchor_weekday=beat.anchor_weekday,
        has_guidance=beat.guidance is not None,
    )
    return beat
