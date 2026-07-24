"""Paths API: create/list/poll/retry/delete a learning path (AL-050, TDD §6).

The learner-facing surface over the generation orchestrator (§5.4). Layering
(CLAUDE.md): this router calls the ``services`` orchestrator + read seam + the
``repositories`` data layer — never the agents or the DB directly. Every route is
session-cookie protected (``get_current_user`` → ``401`` via the shared envelope)
and addresses by UUID; a resource owned by another learner reads as ``404``
(never ``403`` — its existence is not disclosed, TDD §6), resolved once by the
shared ``OwnedPath`` dependency.

**Trigger + poll (§5.4/D5).** ``POST /paths`` and ``POST /paths/{id}/retry``
*trigger* generation and return ``202`` immediately; the client polls
``GET /paths/{id}`` until ``status`` resolves (``ready``/``failed``/``refused``).
No route blocks on a model call. The poll target is itself a trigger: reading a
path spawns the same idempotent resume, so a chain lost to a crash self-heals
within one poll.

**The orchestrator is the module-level singleton** (``generation_orchestrator``):
AL-041's lifespan binds its ``spawn``/``model_slot`` seams in place, so importing
it here routes every background trigger through the task registry (strong refs,
shutdown cancel) and the process-wide concurrency semaphore. It is deliberately
not reconstructed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003 - FastAPI resolves route-param annotations.

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import (  # noqa: TC002 - FastAPI resolves annotations.
    AsyncSession,
)

from aleph.authz import is_admin
from aleph.config import settings
from aleph.db import get_session
from aleph.dependencies import get_current_user
from aleph.dtos.paths import (
    CreatePathRequest,
    CreatePathResponse,
    LessonSummaryDTO,
    PathDetailResponse,
    PathListResponse,
    PathProgressDTO,
    PathSummaryDTO,
    UnitDTO,
)
from aleph.models import (  # noqa: TC001 - FastAPI resolves annotations.
    Path,
    User,
)
from aleph.repositories import (
    LessonRepository,
    PathRepository,
)
from aleph.services.generation import generation_orchestrator
from aleph.services.paths_read import load_path_detail
from aleph.services.rate_limit import build_daily_rate_limiter

if TYPE_CHECKING:
    from aleph.repositories import PathGenerationProgress
    from aleph.services.paths_read import PathDetailView

router = APIRouter(prefix="/api/v1", tags=["paths"])

CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]


def _path_not_found() -> HTTPException:
    """A ``404`` for a path the caller does not own or that does not exist.

    Ownership failures return ``404`` (not ``403``) so a learner cannot probe
    which UUIDs belong to others (TDD §6). Rendered through the shared envelope
    as ``{"error": {"code": "not_found", ...}}``.
    """
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="path not found")


async def get_owned_path(path_id: UUID, user: CurrentUser, session: Session) -> Path:
    """Resolve ``path_id`` only if it belongs to the caller, else ``404``.

    The single ownership seam behind ``GET``/``retry``/``DELETE`` (TDD §6): each
    resolves the owned row once through this dependency rather than repeating the
    fetch-or-404 triplet. ``get_current_user`` runs first, so an anonymous
    request is rejected with ``401`` before ownership is ever considered.
    """
    path = await PathRepository(session).get_for_user(path_id=path_id, user_id=user.id)
    if path is None:
        raise _path_not_found()
    return path


OwnedPath = Annotated[Path, Depends(get_owned_path)]


def _progress_dto(progress: PathGenerationProgress) -> PathProgressDTO:
    """Map the repository roll-up to its wire DTO.

    Explicit construction (not ``model_validate(from_attributes=True)``) is the
    single chosen mapping style here: it keeps the DTO decoupled from the
    ``PathGenerationProgress`` shape (whose ``total``/``generated`` are derived
    properties over ``by_state``) rather than binding the wire contract to it.
    """
    return PathProgressDTO(
        total_lessons=progress.total_lessons,
        generated_lessons=progress.generated_lessons,
        completed_lessons=progress.completed_lessons,
    )


@router.post("/paths", status_code=status.HTTP_202_ACCEPTED)
async def create_path(
    body: CreatePathRequest, user: CurrentUser, session: Session
) -> CreatePathResponse:
    """Create a path and trigger its outline → ``202 {id}`` (W1; rate-limited).

    The daily per-account cap is checked *before* the billed work (admins exempt,
    TDD §10); a breach raises ``429`` with the ``rate_limited`` envelope. On pass
    the orchestrator inserts the ``pending`` row, spawns the outline task, and
    returns immediately — the client polls ``GET /paths/{id}`` for the outcome.
    """
    limiter = build_daily_rate_limiter(session)
    await limiter.check_path_creation(
        user_id=user.id, is_admin=is_admin(user, settings)
    )
    path = await generation_orchestrator.create_path(
        user_id=user.id, topic=body.topic, level=body.level
    )
    return CreatePathResponse(id=path.id)


@router.get("/paths")
async def list_paths(user: CurrentUser, session: Session) -> PathListResponse:
    """The "Your paths" switcher: topic, level, status, progress (§6).

    Two queries regardless of path count: the learner's paths (newest first) with
    their **effective** status, and a single grouped progress roll-up over all of
    them. ``status`` is effective (a stale ``generating`` reads as ``failed``),
    matching the detail poll — so the switcher and the path view never disagree on
    a crashed outline.
    """
    paths = await PathRepository(session).list_for_user_with_effective_status(
        user_id=user.id
    )
    summaries = await LessonRepository(session).progress_summaries(
        [path.id for path, _ in paths]
    )
    return PathListResponse(
        paths=[
            PathSummaryDTO(
                id=path.id,
                topic=path.topic,
                level=path.level,
                status=effective_status,
                progress=_progress_dto(summaries[path.id]),
            )
            for path, effective_status in paths
        ]
    )


def _detail_response(path: Path, view: PathDetailView) -> PathDetailResponse:
    """Translate the composed read-seam view + owned row to the wire DTO."""
    return PathDetailResponse(
        id=path.id,
        topic=path.topic,
        level=path.level,
        status=view.status,
        refusal_message=view.refusal_message,
        progress=_progress_dto(view.progress),
        units=[
            UnitDTO(
                id=unit.id,
                title=unit.title,
                summary=unit.summary,
                position=unit.position,
                lessons=[
                    LessonSummaryDTO(
                        id=lesson.id,
                        title=lesson.title,
                        position_in_path=lesson.position_in_path,
                        position_in_unit=lesson.position_in_unit,
                        generation_state=lesson.generation_state,
                        unlock_state=lesson.unlock_state,
                    )
                    for lesson in unit.lessons
                ],
            )
            for unit in view.units
        ],
    )


@router.get("/paths/{path_id}")
async def get_path(path: OwnedPath, session: Session) -> PathDetailResponse:
    """Poll target: effective status, outline, per-lesson state + unlock, progress.

    Ownership is resolved by ``OwnedPath`` (``404`` otherwise). The composition —
    poll-as-trigger resume, effective status/refusal/progress snapshot, and the
    outline with each lesson's effective generation state and derived unlock
    state — lives in ``services.paths_read.load_path_detail`` (the read seam
    AL-051 reuses); this route only translates the result to the DTO. A ``None``
    view means the path was deleted between the ownership read and the poll (a
    raced delete) → ``404``.
    """
    view = await load_path_detail(session, generation_orchestrator, path.id)
    if view is None:
        raise _path_not_found()
    return _detail_response(path, view)


@router.post("/paths/{path_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_path(
    path: OwnedPath, user: CurrentUser, session: Session
) -> CreatePathResponse:
    """Retry a ``failed`` outline → ``202 {id}`` (W8; trigger + poll; rate-limited).

    Ownership via ``OwnedPath`` (``404`` otherwise). This is a billed trigger that
    inserts no row, so it carries its **own** daily cap
    (``check_outline_generation``, admins exempt) *before* triggering — a breach
    raises ``429`` with the ``rate_limited`` envelope. The cap counts paths with
    an outline attempt today and bounds cross-path retry storms; a same-path retry
    loop is bounded only by claim serialization + client patience (see
    ``services.rate_limit``).

    On pass the re-claim is *triggered*, not awaited, through the orchestrator's
    public ``trigger_outline_retry`` (the registry-bound ``spawn`` seam create
    uses), so the request returns immediately and the client polls
    ``GET /paths/{id}`` for the result — never blocking the HTTP worker on a model
    call (§5.4/D5). ``retry_outline`` re-claims only a ``pending``/``failed``
    outline; on a terminal ``ready``/``refused``/fresh-``generating`` path the
    claim is a **silent no-op** (a refusal is terminal — the learner starts a new
    topic, §5.5), so a stray retry still returns ``202`` but changes nothing,
    rather than being an error to model in the route.
    """
    limiter = build_daily_rate_limiter(session)
    await limiter.check_outline_generation(
        user_id=user.id, is_admin=is_admin(user, settings)
    )
    generation_orchestrator.trigger_outline_retry(path.id)
    return CreatePathResponse(id=path.id)


@router.delete("/paths/{path_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_path(path: OwnedPath, session: Session) -> Response:
    """Hard-delete a path; the cascade tears down its whole tree (W5, §4).

    Ownership via ``OwnedPath`` (``404`` otherwise). ``DELETE paths`` cascades to
    units, lessons, quick checks, and attempts (ON DELETE CASCADE); other paths
    are untouched. Not undoable (the UI confirms); doubles as reset (CONTEXT.md).
    """
    await PathRepository(session).delete(path.id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
