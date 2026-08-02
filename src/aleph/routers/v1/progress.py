"""Progress API: the streak summary (Phase 5 TDD §6, D4).

The Streaks slice's one route. Conventions copied verbatim from
``routers/v1/shaping.py`` (itself following ``routers/v1/paths.py``): session-
cookie auth (``get_current_user`` -> ``401`` through the shared envelope), and
the router-level flag gate that hides the whole surface — currently one route,
but router-level so a future addition to this file inherits the gate by
construction rather than by remembering to add it.

**The flag gate.** Every route here hangs off ``require_streaks_enabled``: when
the ``streaks`` flag resolves **off** for the caller, the surface answers
``404`` — for that account it does not exist, same posture as ``tutor`` and
``shaping`` (D7). ``get_current_user`` is a dependency of the gate, so an
anonymous request is ``401`` before the flag is ever consulted.

**Read-only, no writes.** This router — and everything under it in the call
graph (``services/progress_read.py``, ``LessonRepository.completion_days_for_user``)
— holds no ``session.commit()`` and calls no repository mutator (§3's structural
claim: nothing here writes). The single route is a ``GET``, and there is no
scheduler, push, or background job anywhere near it (PRD §3's restraint list).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import (  # noqa: TC002 - FastAPI resolves annotations.
    AsyncSession,
)

from aleph.db import get_session
from aleph.dependencies import get_current_user
from aleph.dtos.progress import (
    ActivityCellDTO,
    PathStreakDTO,
    ProgressSummaryResponse,
    TzOffsetMinutes,
)
from aleph.models import User  # noqa: TC001 - FastAPI resolves annotations.

# Same precedent ``routers/v1/shaping.py`` follows for its own flag gate:
# imported rather than copied, because two spellings of "a 404 through the
# shared envelope" is how one rail starts disclosing existence the other
# hides.
from aleph.routers.v1.tutor import _not_found
from aleph.services.feature_flags import FeatureFlag, FeatureFlagService
from aleph.services.progress_read import load_progress_summary

if TYPE_CHECKING:
    from aleph.services.progress_read import ProgressSummaryView

CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]


async def require_streaks_enabled(user: CurrentUser, session: Session) -> None:
    """Hide the entire Progress surface unless the ``streaks`` flag resolves on.

    Mounted as a **router-level** dependency (see the module docstring).
    Resolution order lives in ``services/feature_flags`` — per-user override >
    ``FEATURE_FLAG_DEFAULTS`` > admin default > code default — the same chain
    that let ``tutor`` and ``shaping`` ship dark and be dogfooded by admins
    before launch; ``streaks`` followed the identical playbook (D7) and is now
    launched too, which makes this gate a kill switch rather than a curtain.
    """
    flags = await FeatureFlagService(session).resolve_for_user(user)
    if not flags.get(FeatureFlag.STREAKS, False):
        raise _not_found()


router = APIRouter(
    prefix="/api/v1",
    tags=["progress"],
    dependencies=[Depends(require_streaks_enabled)],
)


def _progress_summary_response(view: ProgressSummaryView) -> ProgressSummaryResponse:
    """Map the composed read-seam view to its wire DTO.

    Explicit construction (not ``model_validate(from_attributes=True)``), the
    single chosen mapping style in this codebase (``_progress_dto`` in
    ``routers/v1/paths.py``): it keeps the DTO decoupled from the service's
    frozen-dataclass shape rather than binding the wire contract to it.
    """
    return ProgressSummaryResponse(
        today=view.today,
        current_streak=view.current_streak,
        best_streak=view.best_streak,
        completed_today=view.completed_today,
        activity=[
            ActivityCellDTO(date=cell.day, count=cell.count) for cell in view.activity
        ],
        paths=[
            PathStreakDTO(
                path_id=path.path_id,
                current_streak=path.current_streak,
                best_streak=path.best_streak,
                completed_today=path.completed_today,
            )
            for path in view.paths
        ],
    )


@router.get("/progress/summary")
async def get_progress_summary(
    user: CurrentUser,
    session: Session,
    tz_offset_minutes: TzOffsetMinutes = 0,
) -> ProgressSummaryResponse:
    """The global streak, the activity window and the per-path breakdown (§6).

    ``tz_offset_minutes`` is the client's ``getTimezoneOffset()`` value
    verbatim (D3), validated to ``[-900, 900]`` at the DTO boundary — an
    out-of-range value is a ``422`` through the shared envelope before this
    body ever runs. Defaults to ``0`` (UTC) so a caller that omits it gets a
    coherent, if not learner-local, answer rather than a required-field error.

    No caching, no rate limiting (§7): the cost is bounded by the caller's own
    completion history, and there is no model call to guard against.
    """
    view = await load_progress_summary(
        session, user_id=user.id, tz_offset_minutes=tz_offset_minutes
    )
    return _progress_summary_response(view)
