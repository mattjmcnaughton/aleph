"""Tutor API: send a turn, read/clear the thread, answer a Tutor check (§6).

The tutor's HTTP surface, layered like the paths and lessons routers
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

**One route streams** (AL-220): ``POST …/conversation/messages`` answers
``text/event-stream`` and hands the reply back as it is produced (§5.4/D1). The
split that keeps that honest is admission versus streaming — everything that can
still be an ordinary JSON error (auth, ownership, the picker, lesson state, the
in-flight conflict, the daily cap) happens in ``tutor_turn_service.admit``,
*before* a response object exists; once the ``200`` is committed the only way to
report a failure is an ``error`` event. All of that lifecycle lives in
``services/tutor.py``: this route resolves the caller, gates the picker, frees
the request's database session, and returns the response.

**Two things a streaming route owns that a JSON one does not**, both here rather
than in the service because both are properties of the *response object*:

* :class:`ReservedStream` — the conversation's D9 reservation is released in a
  ``finally`` around the response's own ``__call__``, the one frame ASGI
  guarantees will run. The body generator is not that frame: Starlette can
  create this response and then cancel it before the first ``__anext__`` (a
  client that disconnects between admission and the first byte), and an async
  generator that never started never runs its ``finally`` — not even on an
  explicit ``aclose`` (PEP 525). Releasing there would wedge the conversation on
  a permanent ``409`` until the process restarted.
* **closing the request's session before returning.** ``OwnedPath`` resolves
  ownership with a ``SELECT``, which autobegins a transaction and pins a pooled
  connection until FastAPI unwinds the dependency stack — and for a streaming
  response that unwind happens *after* the stream ends, up to
  ``TUTOR_REPLY_TIMEOUT`` later. At ``MAX_CONCURRENT_TUTOR_REPLIES`` plus
  everything queued behind them, that is how a streaming endpoint takes the rest
  of the API down with it.

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
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import (  # noqa: TC002 - FastAPI resolves annotations.
    AsyncSession,
)

from aleph.authz import is_admin
from aleph.config import settings
from aleph.db import get_session
from aleph.dependencies import get_current_user
from aleph.dtos.tutor import (
    ConversationResponse,
    MessageDTO,
    SendMessageRequest,
    TutorCheckAnswerRequest,
    TutorCheckDTO,
)
from aleph.models import User  # noqa: TC001 - FastAPI resolves annotations.
from aleph.repositories import ConversationRepository
from aleph.routers.v1.paths import (  # noqa: TC001 - FastAPI resolves annotations.
    OwnedPath,
    validate_model_override,
)
from aleph.services.feature_flags import FeatureFlag, FeatureFlagService
from aleph.services.sse import SSE_HEADERS, SSE_MEDIA_TYPE
from aleph.services.tutor import tutor_turn_service

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from starlette.types import Receive, Scope, Send

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


class ReservedStream(StreamingResponse):
    """A ``StreamingResponse`` that frees its conversation when the response ends.

    The reservation (Phase 2 D9, one in-flight reply per conversation) is claimed
    during admission, before this object exists, and released here in a
    ``finally`` around ``__call__`` — see the module docstring for why the body
    generator's ``finally`` cannot be trusted with it.

    ``release`` takes the claim's *token*, not the path id: between this
    response's release and any later duplicate, a new request can legitimately
    reserve the same conversation, and a keyed discard would free the
    successor's claim. The token makes a late release a genuine no-op.
    """

    def __init__(
        self,
        content: AsyncIterator[str],
        *,
        release: Callable[[], None],
        media_type: str,
        headers: dict[str, str],
    ) -> None:
        super().__init__(content, media_type=media_type, headers=headers)
        self._release = release

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._release()


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


@router.post(
    "/paths/{path_id}/conversation/messages",
    response_class=StreamingResponse,
)
async def send_message(
    path: OwnedPath,
    body: SendMessageRequest,
    user: CurrentUser,
    session: Session,
) -> StreamingResponse:
    """Send a turn → ``text/event-stream``, the reply as it is produced (§5.4).

    Ownership via ``OwnedPath`` (``404`` otherwise). The gates run in this order,
    and the order is the contract:

    1. **The picker**, first and before any billed work — a ``model`` override is
       admin-only (``403``, checked before the allowlist so a non-admin never
       learns its shape) and allowlist-bound (``422``). It is resolved **per
       request and persisted nowhere**: Phase 1 pins its choice on the path row
       because background resume has to route the same model, while a tutor reply
       is request-scoped (D2), so there is nothing to resume and no column to
       add. Which model served a reply is recoverable from the pydantic-ai span.
    2. **Admission** (``services/tutor.py``): the lesson is on the path and
       generated (``404``/``409``), the conversation has no reply in flight
       (``409``, D9), the learner is under the daily cap (``429``, D8), and the
       context assembles. All of these are ordinary JSON error envelopes —
       **SSE starts only once the turn is admitted**, which is what makes a
       pre-stream failure something a normal fetch error handler can read.
    3. **The stream**: ``delta`` / ``tutor_check`` frames, a ``: ping`` comment
       through model silence, and exactly one terminal ``done`` (the turn is
       persisted, both ids on the wire) or ``error`` (nothing persisted, D2).

    ``session`` is declared here only to be *closed*: FastAPI caches a dependency
    per request, so this is the very instance ``OwnedPath`` resolved ownership
    on, and closing it hands its pooled connection back before the stream opens
    rather than when the dependency stack unwinds (module docstring). Closing is
    idempotent, so the stack's own later teardown is a harmless no-op, and
    everything downstream of here uses its own short-lived sessions.

    The response is deliberately not a ``response_model``: its body is an event
    stream, and the payload shapes that ride in it are the ``dtos/tutor.py``
    stream DTOs rather than one envelope OpenAPI could describe.
    """
    admin = is_admin(user, settings)
    model_id = (
        validate_model_override(
            body.model, is_admin=admin, allowed=settings.allowlist_ids
        )
        or settings.model_tutor
    )
    turn = await tutor_turn_service.admit(
        path=path,
        is_admin=admin,
        lesson_id=body.lesson_id,
        content=body.content,
        source=body.source,
        model_id=model_id,
    )
    # After admission: ``admit`` still reads ``path``'s loaded columns, and it is
    # the last thing that does.
    await session.close()
    return ReservedStream(
        tutor_turn_service.stream(turn),
        release=lambda: tutor_turn_service.release(turn),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
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
