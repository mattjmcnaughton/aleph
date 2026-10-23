"""Path and Beat creation transactions, followed by target-owned dispatch.

Inputs have passed DTO and model-picker validation at the boundary. Insertion
uses the request session: no nested creation transaction can escape its commit.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from aleph import events
from aleph.domains.learning_recommendations import RecommendationConflict
from aleph.dtos.learning_tutor import EvidenceReference
from aleph.models import Beat, Path
from aleph.repositories import BeatRepository, PathRepository, UserRepository
from aleph.repositories.learning_tutor import LearningTutorRepository
from aleph.services.briefing import briefing_service
from aleph.services.feature_flags import FeatureFlag, FeatureFlagService
from aleph.services.generation import generation_orchestrator
from aleph.services.learning_tutor import LearningTutorUnavailable
from aleph.services.rate_limit import build_daily_rate_limiter

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.dtos.beats import DeployBeatRequest
    from aleph.dtos.paths import CreatePathRequest
    from aleph.models import User
    from aleph.models.learning_tutor import LearningRecommendation, LearningTutorState


class AcceptedTargetDeleted(ValueError):  # noqa: N818 - domain outcome
    """The acceptance survives its explicitly deleted target."""


def creation_hash(body: CreatePathRequest | DeployBeatRequest) -> str:
    # DTO normalization is also what is stored on the target. Do not casefold
    # prose or erase meaningful interior whitespace. Omitted and explicit null
    # defaults are equivalent; only creation inputs (including overrides) count.
    payload = body.model_dump(
        mode="json",
        exclude={"learning_recommendation_id", "learning_tutor_revision"},
    )
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def prepare_acceptance(
    session: AsyncSession,
    user: User,
    body: CreatePathRequest | DeployBeatRequest,
    kind: str,
) -> tuple[
    LearningTutorState | None, LearningRecommendation | None, Path | Beat | None
]:
    # All ordinary and recommended admissions share this lock and the same caps.
    if await UserRepository(session).lock_for_creation(user.id) is None:
        raise LearningTutorUnavailable()
    flags = await FeatureFlagService(session).resolve_for_user(user)
    if kind == "beat" and not flags.get(FeatureFlag.ANALYST):
        raise LearningTutorUnavailable()
    if body.learning_recommendation_id is None:
        return None, None, None
    if not flags.get(FeatureFlag.LEARNING_TUTOR):
        raise LearningTutorUnavailable()
    repo = LearningTutorRepository(session)
    state = await repo.lock_state(user.id)
    row = await repo.recommendation(user.id, body.learning_recommendation_id, lock=True)
    if row is None:
        raise LearningTutorUnavailable()
    if row.kind != kind:
        raise RecommendationConflict("recommendation_unavailable")
    if row.status == "accepted":
        if row.accepted_request_hash != creation_hash(body):
            raise RecommendationConflict("request_reused")
        target_id = row.accepted_path_id if kind == "path" else row.accepted_beat_id
        target = (
            await session.get(Path if kind == "path" else Beat, target_id)
            if target_id
            else None
        )
        if target is None:
            raise AcceptedTargetDeleted()
        return state, row, target
    if state.revision != body.learning_tutor_revision:
        raise RecommendationConflict("stale_state")
    if (
        row.status != "active"
        or row.epoch != state.epoch
        or row.focus_version != state.focus_version
    ):
        raise RecommendationConflict("recommendation_unavailable")
    # Account locking does not cover delete routes. Hold the actual evidence
    # rows through commit so deletion either wins first or follows acceptance.
    refs = [EvidenceReference.model_validate(raw) for raw in row.evidence or []]
    for ref in sorted(refs, key=lambda ref: (ref.kind, str(ref.id))):
        if not await repo.source_exists(user.id, ref, lock=True):
            raise RecommendationConflict("recommendation_unavailable")
    return state, row, None


async def accept_created(
    session: AsyncSession,
    state: LearningTutorState | None,
    row: LearningRecommendation | None,
    target: Path | Beat,
    body: CreatePathRequest | DeployBeatRequest,
) -> None:
    if row is None or state is None:
        return
    row.status = "accepted"
    row.accepted_request_hash = creation_hash(body)
    row.accepted_at = await LearningTutorRepository(session).now()
    if isinstance(target, Path):
        row.accepted_path_id = target.id
    else:
        row.accepted_beat_id = target.id
    state.revision += 1
    await session.flush()


def emit_acceptance(row: LearningRecommendation | None, target: Path | Beat) -> None:
    if row is not None:
        events.emit_learning_recommendation_accepted(
            account_id=row.user_id,
            recommendation_id=row.id,
            target_kind=row.kind,
            target_id=target.id,
        )


async def create_path(
    session: AsyncSession, user: User, body: CreatePathRequest, *, is_admin: bool
) -> Path:
    state, row, replay = await prepare_acceptance(session, user, body, "path")
    if replay is not None:
        assert isinstance(replay, Path)
        await session.commit()
        return replay
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
    await accept_created(session, state, row, path, body)
    await session.commit()
    emit_acceptance(row, path)
    try:
        generation_orchestrator.dispatch_created_path(path)
    except Exception:
        # Creation is durable. Never tell the caller it did not happen.
        await PathRepository(session).mark_dispatch_failed(path.id)
        await session.commit()
    return path


async def create_beat(
    session: AsyncSession,
    user: User,
    body: DeployBeatRequest,
    *,
    is_admin: bool,
    local_today: date,
) -> Beat:
    state, row, replay = await prepare_acceptance(session, user, body, "beat")
    if replay is not None:
        assert isinstance(replay, Beat)
        await session.commit()
        return replay
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
    await accept_created(session, state, row, beat, body)
    await session.commit()
    emit_acceptance(row, beat)
    events.emit_beat_deployed(
        account_id=user.id,
        beat_id=beat.id,
        beat_level=beat.level.value,
        anchor_weekday=beat.anchor_weekday,
        has_guidance=beat.guidance is not None,
    )
    try:
        await briefing_service.trigger_initial(
            beat.id, user_id=user.id, local_today=local_today, is_admin=is_admin
        )
    except Exception:
        # trigger_initial fences failures after claiming. A failure before the
        # claim becomes explicitly retryable without undoing its receipt.
        await BeatRepository(session).mark_initial_dispatch_failed(beat.id)
        await session.commit()
    # A concurrent explicit deletion may follow the commit. Do not turn that
    # successful creation into a refresh exception; replay will report 410.
    return await session.get(Beat, beat.id, populate_existing=True) or beat
