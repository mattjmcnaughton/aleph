"""Tutor API: read/clear the conversation, answer a Tutor check (AL-221, §6).

The tutor's non-streaming surface, layered like the paths and lessons routers
(CLAUDE.md: routers -> services -> repositories). Phase 1's conventions apply
verbatim: session-cookie auth (``get_current_user`` → ``401`` via the shared
envelope), UUID addressing, and a resource owned by another learner reading as
``404`` — never ``403``, its existence is not disclosed (TDD §6). Path ownership
is the *same* ``OwnedPath`` dependency the paths router defines, reused rather
than respelled.

**The flag gate (epic #82, owner amendment 1).** Every route here hangs off a
router-level ``require_tutor_enabled`` dependency: when the ``tutor`` feature
flag (AL-203) resolves **off** for the caller, the whole surface answers ``404``
— for that account it does not exist, which is what "ships dark" has to mean
from the outside. It is a router-level dependency and not a per-route one so
AL-220's streamed send endpoint inherits it by construction; a new route cannot
forget the gate. ``404`` rather than ``403`` for the same reason ownership is:
``403`` would confirm the feature exists and merely isn't yours.

**The tutor never writes Phase 1 state** (TDD §3). Recording a Tutor-check
answer reassigns one JSONB payload on one message and nothing else — no
Attempt, no progress, no path structure. That is a property of what this module
imports, not a convention: there is no code path from here into lessons,
attempts, or progression.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003 - FastAPI resolves route-param annotations.

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import (  # noqa: TC002 - FastAPI resolves annotations.
    AsyncSession,
)

from aleph.db import get_session
from aleph.dependencies import get_current_user
from aleph.dtos.tutor import (
    ConversationResponse,
    MessageDTO,
    TutorCheckAnswerRequest,
    TutorCheckDTO,
)
from aleph.models import User  # noqa: TC001 - FastAPI resolves annotations.
from aleph.repositories import ConversationRepository
from aleph.routers.v1.paths import (  # noqa: TC001 - FastAPI resolves annotations.
    OwnedPath,
)
from aleph.services.feature_flags import FeatureFlag, FeatureFlagService

if TYPE_CHECKING:
    from aleph.models import Message
    from aleph.repositories import ThreadMessage

CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]


def _not_found(detail: str = "not found") -> HTTPException:
    """A ``404`` rendered through the shared envelope as ``not_found``."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


async def require_tutor_enabled(user: CurrentUser, session: Session) -> None:
    """Hide the entire tutor surface unless the ``tutor`` flag resolves on.

    Mounted as a **router-level** dependency (see the module docstring), so it
    covers every current and future tutor route. Resolution order lives in
    ``services/feature_flags`` — per-user override > ``FEATURE_FLAG_DEFAULTS`` >
    admin default > code default — which is what lets admins dogfood the tutor
    in production while it is off for everyone else.

    ``get_current_user`` is a dependency of this one, so an anonymous request is
    ``401`` before the flag is ever consulted: "sign in" is the honest answer
    there, and a signed-out prober learns nothing about the flag either way.
    """
    flags = await FeatureFlagService(session).resolve_for_user(user)
    if not flags.get(FeatureFlag.TUTOR, False):
        raise _not_found()


router = APIRouter(
    prefix="/api/v1",
    tags=["tutor"],
    dependencies=[Depends(require_tutor_enabled)],
)


def _check_dto(payload: dict[str, object] | None) -> TutorCheckDTO | None:
    """Map a stored ``tutor_check`` payload to its wire DTO.

    ``None`` on every learner row and on a tutor reply that posed no check.
    Validation (rather than construction) is deliberate: the payload was written
    by the agent tool, so this is where a shape that predates a field — an
    ``answered_index`` key that was never stored, say — picks up its default.
    """
    if payload is None:
        return None
    return TutorCheckDTO.model_validate(payload)


def _ensure_answerable(message: Message, *, selected_index: int) -> None:
    """``409`` when there is no check to answer; ``422`` when the index misses."""
    check = message.tutor_check
    if check is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="message has no tutor check",
        )
    options = check.get("options") or []
    if selected_index >= len(options):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"selected_index must be between 0 and {len(options) - 1}",
        )


def _message_dto(entry: ThreadMessage) -> MessageDTO:
    """Translate one repository thread row (message + lesson title) to the wire."""
    message = entry.message
    return MessageDTO(
        id=message.id,
        role=message.role,
        content=message.content,
        lesson_id=message.lesson_id,
        lesson_title=entry.lesson_title,
        tutor_check=_check_dto(message.tutor_check),
        created_at=message.created_at,
    )


@router.get("/paths/{path_id}/conversation")
async def get_conversation(path: OwnedPath, session: Session) -> ConversationResponse:
    """The path's whole thread, oldest first (§6).

    Ownership via ``OwnedPath`` (``404`` otherwise). A path with no conversation
    yet is ``200`` with an empty list, not ``404``: the row is created lazily on
    the first completed turn (TDD §4), so an untouched path and a cleared one
    read identically — which is exactly what "new conversation" should leave
    behind.

    One query serves it: the repository resolves each message's lesson title in
    the thread join rather than looking it up per message. Unpaginated this
    phase (an accepted risk, TDD §14).
    """
    thread = await ConversationRepository(session).load_thread(path.id)
    return ConversationResponse(messages=[_message_dto(entry) for entry in thread])


@router.delete("/paths/{path_id}/conversation", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(path: OwnedPath, session: Session) -> Response:
    """**New conversation**: drop the thread (PRD §5.8) → ``204``, idempotent.

    Ownership via ``OwnedPath`` (``404`` otherwise). Deleting the conversation
    row cascades its messages; the path, its lessons, and every scrap of Phase 1
    state are untouched (TDD §3). Clearing an already-empty thread is ``204``
    too — the affordance is one tap, retries and double taps must not read as
    errors.

    It never refunds quota (TDD D8): the tutor cap is disabled at its default of
    0, so no usage rows exist to refund. That the count would be refundable *if*
    the cap were enabled is the recorded precondition for enabling it, not work
    to do here.
    """
    await ConversationRepository(session).delete_for_path(path.id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/messages/{message_id}/tutor-check-answer",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def answer_tutor_check(
    message_id: UUID,
    body: TutorCheckAnswerRequest,
    user: CurrentUser,
    session: Session,
) -> Response:
    """Record the learner's Tutor-check choice → ``204`` (PRD §5.5, W12).

    Ownership walks message → conversation → path → user in one join, so
    someone else's message is indistinguishable from a missing one (``404``).
    Guards, in order:

    * a message with no Tutor check → ``409``. The row exists and is the
      caller's; the request conflicts with its state (Phase 1's "not generated
      yet" precedent), so it is neither ``404`` nor ``422``.
    * an index outside the stored ``options`` → ``422``. Nothing grades a Tutor
      check, so ``selected_index`` is only ever used to index those options when
      re-rendering the revealed card; an unindexable value would store fine and
      break that render later.

    The write is a **reassignment** of the JSONB payload
    (``set_tutor_check_answer``) — plain ``JSONB`` has no ORM mutation tracking,
    so an in-place edit would never be flushed. Nothing else is written: no
    Attempt, no progress, no lesson state. A Tutor check is non-scoring and
    outside progress, which is a property of this schema rather than a
    convention (TDD §4).

    Answering twice overwrites, deliberately unlike the Quick check's first-wins
    Attempt: first-wins exists because an Attempt is graded and feeds the §7
    metrics, and neither is true here. (``tutor_check_answered`` lands with
    AL-240, which owns whether a re-answer re-emits.)
    """
    repository = ConversationRepository(session)
    message = await repository.get_message_for_user(
        message_id=message_id, user_id=user.id
    )
    if message is None:
        raise _not_found("message not found")
    _ensure_answerable(message, selected_index=body.selected_index)
    await repository.set_tutor_check_answer(
        message=message, selected_index=body.selected_index
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
