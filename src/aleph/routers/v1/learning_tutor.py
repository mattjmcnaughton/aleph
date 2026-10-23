"""Authenticated, dark-gated Learning Tutor state API (LT-1 only)."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from aleph.db import get_session
from aleph.dependencies import get_current_user
from aleph.domains.learning_recommendations import RecommendationConflict
from aleph.dtos.learning_tutor import (
    DecisionRequest,
    FocusRequest,
    HistoryPage,
    LearningTutorStateDTO,
    OperationDTO,
    RecommendationDTO,
    RecommendationPage,
)
from aleph.models import User
from aleph.services.feature_flags import FeatureFlag, FeatureFlagService
from aleph.services.learning_tutor import LearningTutorService, LearningTutorUnavailable

CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]


async def require_enabled(user: CurrentUser, session: Session) -> None:
    if not (await FeatureFlagService(session).resolve_for_user(user)).get(
        FeatureFlag.LEARNING_TUTOR
    ):
        raise HTTPException(404, "not found")


async def service(user: CurrentUser, session: Session):
    # Authentication/zone observation has committed; the gate only reads flags.
    # Release its connection before the service opens a read-only snapshot, so
    # concurrent requests never hold one pool slot while waiting for another.
    await session.close()
    try:
        yield LearningTutorService(session, user)
    except LearningTutorUnavailable:
        raise HTTPException(404, "not found") from None
    except RecommendationConflict as exc:
        raise HTTPException(
            409,
            {
                "message": "Learning Tutor state changed; reload and try again.",
                "reason": str(exc),
            },
        ) from None


Service = Annotated[LearningTutorService, Depends(service)]
router = APIRouter(
    prefix="/api/v1/learning-tutor",
    tags=["learning-tutor"],
    dependencies=[Depends(require_enabled)],
)


@router.get("/")
async def get_state(service: Service) -> LearningTutorStateDTO:
    return await service.read()


@router.put("/focus")
async def save_focus(body: FocusRequest, service: Service) -> LearningTutorStateDTO:
    return await service.save_focus(body.focus, revision=body.revision)


@router.delete("/conversation")
async def clear_conversation(
    service: Service, revision: Annotated[int, Query(ge=0)]
) -> LearningTutorStateDTO:
    return await service.clear(revision=revision)


@router.get("/history")
async def history(service: Service, before: UUID | None = None) -> HistoryPage:
    return await service.history(before)


@router.get("/operations/{operation_id}")
async def operation(operation_id: UUID, service: Service) -> OperationDTO:
    return await service.operation(operation_id)


@router.get("/recommendations")
async def recommendations(
    service: Service, status: Literal["dismissed"], before: UUID | None = None
) -> RecommendationPage:
    return await service.dismissed(before)


@router.get("/recommendations/{recommendation_id}")
async def recommendation(
    recommendation_id: UUID, service: Service
) -> RecommendationDTO:
    return await service.recommendation(recommendation_id)


@router.put("/recommendations/{recommendation_id}/decision")
async def decision(
    recommendation_id: UUID, body: DecisionRequest, service: Service
) -> LearningTutorStateDTO:
    return await service.decide(
        recommendation_id, body.decision, revision=body.revision
    )
