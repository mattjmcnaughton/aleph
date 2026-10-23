"""Authenticated, dark-gated Learning Tutor state and trigger/poll API."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from aleph import events
from aleph.db import get_session
from aleph.dependencies import get_current_user
from aleph.domains.learning_recommendations import RecommendationConflict
from aleph.dtos.learning_tutor import (
    DecisionRequest,
    FocusRequest,
    HistoryPage,
    InteractionRequest,
    LearningTutorStateDTO,
    OperationDTO,
    OperationRequest,
    RecommendationDTO,
    RecommendationPage,
)
from aleph.models import User
from aleph.services.feature_flags import FeatureFlag, FeatureFlagService
from aleph.services.learning_tutor import LearningTutorService, LearningTutorUnavailable
from aleph.services.learning_tutor_generation import (
    LearningTutorBusy,
    LearningTutorQuotaExceeded,
    learning_tutor_generation,
)

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
    except LearningTutorQuotaExceeded:
        raise HTTPException(
            429, "Today's Tutor limit reached; try again tomorrow."
        ) from None
    except LearningTutorBusy as exc:
        raise HTTPException(
            409,
            {
                "reason": "operation_in_progress",
                "operation_id": str(exc.operation_id),
                "message": "A Tutor operation is already running.",
            },
        ) from None
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


@router.post("/operations")
async def start_operation(
    body: OperationRequest, service: Service, response: Response
) -> OperationDTO:
    result = await learning_tutor_generation.admit(service, body)
    response.status_code = 202 if result.status == "running" else 200
    return result


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


@router.post("/recommendations/{recommendation_id}/interactions", status_code=204)
async def interaction(
    recommendation_id: UUID,
    body: InteractionRequest,
    service: Service,
) -> None:
    # Observational, never a decision mutation. The read validates current access.
    await service.recommendation(recommendation_id)
    events.emit_learning_recommendation_interacted(
        account_id=service.user.id,
        recommendation_id=recommendation_id,
        interaction=body.interaction,
        surface=body.surface,
        session_id=body.session_id,
    )
