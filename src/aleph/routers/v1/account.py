"""The learner's account-level profile: About you and the Learning summary.

``/about-you`` is open to every learner. ``/learning-summary`` is gated by the
``learning_summary`` flag (off -> ``404``), mounted per route through
``require_learning_summary_enabled``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from aleph.db import get_session
from aleph.dependencies import get_current_user, require_same_origin
from aleph.dtos.account import (
    AboutYouDTO,
    LearningSummaryBodyDTO,
    LearningSummaryResponse,
    PublicProfileSettingsDTO,
    SummaryFactsDTO,
)
from aleph.errors import error_response
from aleph.models import User  # noqa: TC001 - FastAPI resolves annotations.
from aleph.routers.v1.tutor import _not_found
from aleph.services import about_you
from aleph.services.feature_flags import FeatureFlag, FeatureFlagService
from aleph.services.learning_summary import (
    MIN_LESSONS_COMPLETE,
    NotEnoughProgressError,
    RefreshTooSoonError,
    SummaryView,
    learning_summary_service,
)
from aleph.services.public_profiles import (
    HandleInvalidError,
    HandleLockedError,
    HandleRequiredError,
    HandleTakenError,
    OwnerSettings,
    PublicProfileService,
)

CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]
router = APIRouter(prefix="/api/v1", tags=["account"])


async def require_learning_summary_enabled(user: CurrentUser, session: Session) -> None:
    flags = await FeatureFlagService(session).resolve_for_user(user)
    if not flags.get(FeatureFlag.LEARNING_SUMMARY, False):
        raise _not_found()


SummaryGate = Depends(require_learning_summary_enabled)


async def require_public_profiles_enabled(user: CurrentUser, session: Session) -> None:
    """The ``public_profiles`` flag (TDD D9) for the owner routes; off -> ``404``."""
    if not await PublicProfileService(session).enabled_for(user):
        raise _not_found()


PublicProfilesGate = Depends(require_public_profiles_enabled)
SameOrigin = Depends(require_same_origin)


@router.get("/about-you")
async def get_about_you(user: CurrentUser, session: Session) -> AboutYouDTO:
    return AboutYouDTO(about_you=await about_you.get_about_you(session, user.id))


@router.put("/about-you")
async def put_about_you(
    body: AboutYouDTO, user: CurrentUser, session: Session
) -> AboutYouDTO:
    """Replace or clear (``null``) About you. Forward-only: nothing regenerates."""
    saved = await about_you.put_about_you(session, user.id, body.about_you)
    return AboutYouDTO(about_you=saved)


def _summary_response(view: SummaryView) -> LearningSummaryResponse:
    return LearningSummaryResponse(
        state=view.state,
        facts=SummaryFactsDTO(
            lessons_complete=view.facts.lessons_complete,
            paths=view.facts.paths,
            beats=view.facts.beats,
        ),
        summary=(
            LearningSummaryBodyDTO.model_validate(view.body)
            if view.body is not None
            else None
        ),
        written_at=view.written_at,
        min_lessons_complete=MIN_LESSONS_COMPLETE,
    )


@router.get("/learning-summary", dependencies=[SummaryGate])
async def get_learning_summary(
    user: CurrentUser, session: Session
) -> LearningSummaryResponse:
    return _summary_response(await learning_summary_service.read(session, user.id))


@router.post(
    "/learning-summary",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[SummaryGate],
    response_model=LearningSummaryResponse,
)
async def trigger_learning_summary(
    request: Request, user: CurrentUser, session: Session
) -> LearningSummaryResponse | Response:
    """Start writing (or rewriting) the summary; poll ``GET`` for the outcome."""
    try:
        view = await learning_summary_service.trigger(session, user.id)
    except NotEnoughProgressError:
        return error_response(
            request,
            status_code=409,
            code="not_enough_progress",
            message=f"Complete at least {MIN_LESSONS_COMPLETE} lessons first.",
        )
    except RefreshTooSoonError:
        return error_response(
            request,
            status_code=429,
            code="rate_limited",
            message="Your summary was just written. Try again in a few minutes.",
        )
    return _summary_response(view)


def _settings_dto(view: OwnerSettings) -> PublicProfileSettingsDTO:
    return PublicProfileSettingsDTO(
        handle=view.handle,
        profile_public=view.profile_public,
        profile_show_activity=view.profile_show_activity,
    )


@router.get("/account/public-profile", dependencies=[PublicProfilesGate])
async def get_public_profile(
    user: CurrentUser, session: Session
) -> PublicProfileSettingsDTO:
    return _settings_dto(await PublicProfileService(session).settings_for(user))


@router.put(
    "/account/public-profile",
    dependencies=[PublicProfilesGate, SameOrigin],
    response_model=PublicProfileSettingsDTO,
)
async def put_public_profile(
    body: PublicProfileSettingsDTO,
    request: Request,
    user: CurrentUser,
    session: Session,
) -> PublicProfileSettingsDTO | Response:
    """Set the Handle (once), and turn the profile and activity strip on or off."""
    try:
        view = await PublicProfileService(session).update_settings(
            user.id,
            handle=body.handle,
            profile_public=body.profile_public,
            profile_show_activity=body.profile_show_activity,
        )
    except HandleInvalidError as exc:
        return error_response(
            request,
            status_code=422,
            code="handle_rejected",
            message="That handle can't be used.",
            details={"reason": exc.reason},
        )
    except HandleTakenError:
        return error_response(
            request,
            status_code=409,
            code="handle_taken",
            message="That handle is taken.",
        )
    except HandleLockedError:
        return error_response(
            request,
            status_code=409,
            code="handle_locked",
            message="Your handle can't be changed.",
        )
    except HandleRequiredError:
        return error_response(
            request,
            status_code=422,
            code="handle_required",
            message="Pick a handle before turning your profile on.",
        )
    return _settings_dto(view)
