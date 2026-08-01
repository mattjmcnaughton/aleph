"""Shaping API: send a turn, read the thread, start a new conversation (§6).

The **shaping rail**'s HTTP surface — the path-level twin of ``routers/v1/tutor``
and layered the same way (CLAUDE.md: routers -> services -> repositories). Every
Phase 1/2 convention applies verbatim and none is re-decided here: session-cookie
auth (``get_current_user`` → ``401`` through the shared envelope), UUID
addressing, and a resource owned by another learner reading as ``404`` — never
``403``, its existence is not disclosed. Path ownership is the *same*
``OwnedPath`` dependency the paths router defines, reused rather than respelled.

**The flag gate (epic #114, adopted convention 1).** Every route here hangs off a
router-level ``require_shaping_enabled`` dependency: when the ``shaping`` flag
(AL-301) resolves **off** for the caller, the whole surface answers ``404`` — for
that account it does not exist, which is what "ships dark" has to mean from the
outside. Router-level and not per-route so AL-321's apply/undo routes inherit it
by construction; a new route cannot forget the gate. ``404`` rather than ``403``
for the same reason ownership is. It is a **separate** flag from ``tutor``: the
in-lesson tutor is already launched, and shaping must be killable on its own
without disturbing it.

**One route streams**: ``POST …/shaping/conversation/messages`` answers
``text/event-stream`` and hands the reply back as it is produced (§5.4). The
split that keeps that honest is admission versus streaming — everything that can
still be an ordinary JSON error (auth, ownership, the picker, the path's status,
the in-flight conflict, the daily cap) happens in
``shaping_turn_service.admit``, *before* a response object exists; once the
``200`` is committed the only way to report a failure is an ``error`` event.

**Two things a streaming route owns that a JSON one does not**, both here rather
than in the service because both are properties of the *response object* — the
reservation released around the response's own ``__call__``, and closing the
request's session before returning. ``routers/v1/tutor.py``'s module docstring is
the long-form reasoning for both; :class:`ReservedStream` is imported from there
rather than reimplemented, because a second copy of "release the claim in the one
frame ASGI guarantees will run" is a second place for that to go subtly wrong.

**The conversation routes write no path structure** (TDD §3): they reach
conversation rows and nothing else — no unit, no lesson, no attempt, no progress.
**Apply** and **Undo** (AL-321) are the exception this whole phase is about, and
they are still not writers *here*: they resolve ownership, hand ids to
``services/shaping.py``, and translate the result. Every mutation happens in that
service, under its own per-path lock (D11) and in one transaction, so "the only
write path into path structure is Apply on a validated Proposal" stays a property
of module topology rather than of this file's good behaviour.

**Progress is never touched by anything here** (W21's structural guarantee, as in
2A): no route in this module reaches a lesson's ``completed_at`` or an Attempt,
in either direction. Undo can only remove what a Change created, and the
engagement boundary means it cannot even reach that once the learner has met it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003 - FastAPI resolves route-param annotations.

from fastapi import APIRouter, Depends, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import (  # noqa: TC002 - FastAPI resolves annotations.
    AsyncSession,
)

from aleph.authz import is_admin
from aleph.config import settings
from aleph.db import get_session
from aleph.dependencies import get_current_user
from aleph.dtos.shaping import (
    ApplyProposalResponse,
    ChangeDTO,
    ChangeHistoryResponse,
    ProposalDTO,
    SendShapingMessageRequest,
    ShapingConversationResponse,
    ShapingMessageDTO,
)
from aleph.models import (  # noqa: TC001 - FastAPI resolves annotations.
    ConversationKind,
    User,
)
from aleph.repositories import (
    AttemptRepository,
    ChangeRepository,
    ConversationRepository,
    LessonRepository,
    UnitRepository,
)
from aleph.routers.v1.paths import (  # noqa: TC001 - FastAPI resolves annotations.
    OwnedPath,
    path_detail_response,
    validate_model_override,
)

# 2A's router-level helpers, imported rather than copied: ``ReservedStream`` for
# the reason its own docstring gives, and ``_not_found`` because two spellings of
# "a 404 through the shared envelope" is how one rail starts disclosing existence
# the other hides. W21 freezes ``routers/v1/tutor.py`` this phase, so promoting
# the latter to a public name is a mechanical follow-up, not a reason to fork it.
from aleph.routers.v1.tutor import ReservedStream, _not_found
from aleph.services.feature_flags import FeatureFlag, FeatureFlagService
from aleph.services.generation import generation_orchestrator
from aleph.services.paths_read import load_path_detail
from aleph.services.shaping import (
    change_kinds,
    shaping_change_service,
    shaping_turn_service,
)
from aleph.services.sse import SSE_HEADERS, SSE_MEDIA_TYPE
from aleph.services.tutor_context import (
    build_shaping_caps,
    build_shaping_digest,
    change_summary_text,
    derive_proposal_resolutions,
)

if TYPE_CHECKING:
    import uuid
    from collections.abc import Mapping, Sequence

    from aleph.agents.shaper import (
        ProposalResolution,
        ShapingCaps,
        ShapingDigestEntry,
    )
    from aleph.models import Message, Path, PathChange

CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]


async def require_shaping_enabled(user: CurrentUser, session: Session) -> None:
    """Hide the entire shaping surface unless the ``shaping`` flag resolves on.

    Mounted as a **router-level** dependency (see the module docstring), so it
    covers every current and future shaping route. Resolution order lives in
    ``services/feature_flags`` — per-user override > ``FEATURE_FLAG_DEFAULTS`` >
    admin default > code default — which is what lets admins dogfood shaping in
    production while it is off for everyone else, and what makes AL-370's launch
    one environment variable.

    ``get_current_user`` is a dependency of this one, so an anonymous request is
    ``401`` before the flag is ever consulted: "sign in" is the honest answer
    there, and a signed-out prober learns nothing about the flag either way.
    """
    flags = await FeatureFlagService(session).resolve_for_user(user)
    if not flags.get(FeatureFlag.SHAPING, False):
        raise _not_found()


router = APIRouter(
    prefix="/api/v1",
    tags=["shaping"],
    dependencies=[Depends(require_shaping_enabled)],
)


@router.post(
    "/paths/{path_id}/shaping/conversation/messages",
    response_class=StreamingResponse,
)
async def send_shaping_message(
    path: OwnedPath,
    body: SendShapingMessageRequest,
    user: CurrentUser,
    session: Session,
) -> StreamingResponse:
    """Send a shaping turn → ``text/event-stream``, the reply as it is produced.

    Ownership via ``OwnedPath`` (``404`` otherwise). The gates run in this order,
    and the order is the contract:

    1. **The picker**, first and before any billed work — a ``model`` override is
       admin-only (``403``, checked before the allowlist so a non-admin never
       learns its shape) and allowlist-bound (``422``). It is resolved **per
       request and persisted nowhere**, exactly as 2A's is: a reply is
       request-scoped, so there is nothing to resume and no column to add. It
       binds the ``MODEL_SHAPER`` slot (D10).
    2. **Admission** (``services/shaping.py``): the path is ``ready`` (``409`` —
       PRD §5.1's rule, server-enforced), the shaping conversation has no reply
       in flight (``409``, D11), the learner is under the daily cap (``429``,
       §7), and the context assembles. All of these are ordinary JSON error
       envelopes — **SSE starts only once the turn is admitted**, which is what
       makes a pre-stream failure something a normal fetch error handler can
       read.
    3. **The stream**: ``delta`` / ``proposal`` frames, a ``: ping`` comment
       through model silence, and exactly one terminal ``done`` (the turn is
       persisted, both ids on the wire) or ``error`` (nothing persisted).

    ``session`` is declared here only to be *closed*: FastAPI caches a dependency
    per request, so this is the very instance ``OwnedPath`` resolved ownership
    on, and closing it hands its pooled connection back before the stream opens
    rather than when the dependency stack unwinds. Closing is idempotent, so the
    stack's own later teardown is a harmless no-op, and everything downstream of
    here uses its own short-lived sessions.

    The response is deliberately not a ``response_model``: its body is an event
    stream, and the payload shapes that ride in it are the stream DTOs rather
    than one envelope OpenAPI could describe.
    """
    admin = is_admin(user, settings)
    model_id = (
        validate_model_override(
            body.model, is_admin=admin, allowed=settings.allowlist_ids
        )
        or settings.model_shaper
    )
    turn = await shaping_turn_service.admit(
        path=path,
        is_admin=admin,
        content=body.content,
        source=body.source,
        model_id=model_id,
    )
    # After admission: ``admit`` still reads ``path``'s loaded columns, and it is
    # the last thing that does.
    await session.close()
    return ReservedStream(
        shaping_turn_service.stream(turn),
        release=lambda: shaping_turn_service.release(turn),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )


@router.get("/paths/{path_id}/shaping/conversation")
async def get_shaping_conversation(
    path: OwnedPath, session: Session
) -> ShapingConversationResponse:
    """The path's whole shaping thread, oldest first (§6).

    Ownership via ``OwnedPath`` (``404`` otherwise). A path with no shaping
    conversation yet is ``200`` with an empty list, not ``404``: the row is
    created lazily on the first completed turn, so an untouched path and a
    cleared one read identically — which is exactly what "new conversation"
    should leave behind. Unpaginated this phase (an accepted risk, TDD §14).

    **Each Proposal's ``resolution`` is derived here, not stored** (D3), by
    :func:`~aleph.services.tutor_context.derive_proposal_resolutions` — the same
    function that decides how a prior Proposal reads in the shaper's carried
    history. Deriving it twice is how the card and the model would start
    disagreeing about whether an edit already landed, so *superseded* in
    particular costs this route the path reads the derivation needs: a stale
    Proposal is one that no longer validates against **live** path state (the
    shared D1 predicates), which cannot be known from the message row alone.

    Those reads are **skipped entirely on a thread that carries no Proposal** —
    the ordinary case, since most asks are questions. There is no resolution to
    derive without a Proposal to resolve, so the four extra queries would answer
    a question nobody asked; the empty mapping is exactly what the derivation
    returns for such a thread anyway.
    """
    thread = await ConversationRepository(session).load_thread(
        path.id, kind=ConversationKind.SHAPING
    )
    messages = [entry.message for entry in thread]
    resolutions: Mapping[uuid.UUID, ProposalResolution] = {}
    if any(message.proposal is not None for message in messages):
        changes = await ChangeRepository(session).list_for_path(path.id)
        digest, caps = await _live_path_state(session, path=path)
        resolutions = derive_proposal_resolutions(
            messages, changes, digest=digest, caps=caps
        )
    return ShapingConversationResponse(
        messages=[
            _message_dto(message, resolutions=resolutions) for message in messages
        ]
    )


@router.delete(
    "/paths/{path_id}/shaping/conversation",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_shaping_conversation(path: OwnedPath, session: Session) -> Response:
    """**New conversation**: drop the shaping thread (PRD §5.8) → ``204``.

    Ownership via ``OwnedPath`` (``404`` otherwise). Deleting the conversation
    row cascades its messages; the path, its lessons, and every scrap of Phase 1
    state are untouched — and so is the **in-lesson thread**, which is a
    different row of a different kind (D3). Clearing an already-empty thread is
    ``204`` too — the affordance is one tap, and retries and double taps must not
    read as errors.

    **The Change history survives it** (D3, PRD §5.8): ``path_changes`` hangs off
    the path and its ``message_id`` is ``ON DELETE SET NULL``, so the cascade
    nulls the reference and keeps every row. A learner starting a fresh
    conversation does not lose the record of what they already changed — and,
    because applied changes are real path structure, could not.

    It never refunds quota, for 2A's reason: the shaping cap is disabled at its
    default of 0, so no usage rows exist to refund.
    """
    await ConversationRepository(session).delete_for_path(
        path.id, kind=ConversationKind.SHAPING
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/messages/{message_id}/apply-proposal")
async def apply_proposal(
    message_id: UUID, user: CurrentUser, session: Session
) -> ApplyProposalResponse:
    """**Apply** the Proposal on a shaping message → the Change + the fresh path.

    The learner's tap, and the only write path into path structure (§5.6). It
    addresses the *message* rather than the path because the Proposal is what is
    being consented to — a payload validated when it was made, re-validated here
    against live state, and applied whole or not at all.

    Ownership is its **own** walk (message → conversation → path → account), not
    ``OwnedPath``'s and not the Tutor-check route's: a shaping message carries no
    ``lesson_id``, so ``get_message_for_user``'s inner join cannot return one
    (its docstring says so). A message that is not the caller's, is not a shaping
    message, or carries no Proposal is a plain ``404`` — three different facts,
    one answer, because distinguishing them would disclose the first.

    Everything after that is ``services/shaping.py``'s, under the per-path apply
    lock (D11), and every refusal is a ``409`` whose ``details.reason`` is a
    :class:`~aleph.dtos.shaping.ShapingConflictReason` the card renders: already
    applied, stale (with which rule broke), positions shifted, or a target being
    generated right now. §5.8 makes that path first-class UX rather than an error
    corner — a Proposal going stale is *normal* (the learner chats, walks away,
    attempts the target, comes back and taps).

    The response carries the **refreshed path** because the rail is holding ghost
    rows it now has to swap for real ones, and loading it through the same read
    seam ``GET /paths/{id}`` uses is also what kicks Phase 1's prefetch driver —
    §5.6's "so new work starts without waiting for a poll". One round trip,
    no new orchestration (D7).
    """
    owned = await ConversationRepository(session).get_shaping_message_for_user(
        message_id=message_id, user_id=user.id
    )
    if owned is None or not owned.message.proposal:
        raise _not_found()
    path = owned.path
    change_id = await shaping_change_service.apply_change(
        path_id=path.id, message_id=message_id
    )
    view = await load_path_detail(session, generation_orchestrator, path.id)
    change = await ChangeRepository(session).get(change_id)
    if view is None or change is None:  # pragma: no cover - a raced path delete
        raise _not_found()
    return ApplyProposalResponse(
        change=_change_dto(change), path=path_detail_response(path, view)
    )


@router.post("/changes/{change_id}/undo", status_code=status.HTTP_204_NO_CONTENT)
async def undo_change(change_id: UUID, user: CurrentUser, session: Session) -> Response:
    """**Undo** a Change, restoring the path exactly → ``204`` (§5.7).

    Ownership walks change → path → account (``404`` otherwise). The engagement
    re-check (D2) happens in the service, inside the lock and against live state,
    because that is the rule — the history sheet's disabled button is a
    convenience, and a learner can start a lesson between the sheet rendering and
    the tap. A Change whose content has been met answers ``409`` with reason
    ``engaged``, and the sheet says plainly that it is now permanent history
    rather than hiding the affordance (PRD §5.5).

    ``204`` and not the restored path: undo is reached from the history sheet,
    which is a read-only record, and the rail refetches the outline it already
    polls. Apply returns a path because it has ghosts to swap; undo has none.
    """
    owned = await ChangeRepository(session).get_for_user(
        change_id=change_id, user_id=user.id
    )
    if owned is None:
        raise _not_found()
    await shaping_change_service.undo_change(path_id=owned.path.id, change_id=change_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/paths/{path_id}/changes")
async def get_change_history(
    path: OwnedPath, session: Session
) -> ChangeHistoryResponse:
    """The path's **Change history**, newest first (§6).

    Ownership via ``OwnedPath`` (``404`` otherwise). Read-only: it is a record,
    not a second edit surface (PRD §5.5), so undone Changes are listed too —
    undo is a status, and the history is what happened.

    It is scoped by **path**, never by conversation, which is why it answers
    identically before and after "new conversation": the rows outlive the thread
    that produced them (D3), and an applied Change is real path structure that
    clearing a conversation could not take back even if it wanted to.

    ``200`` with an empty list on a path nothing has ever shaped — and on a
    non-``ready`` path, for the conversation read's reason: the ``ready`` rule
    bounds sending, not reading.
    """
    changes = await ChangeRepository(session).list_for_path(path.id)
    return ChangeHistoryResponse(changes=[_change_dto(change) for change in changes])


def _change_dto(change: PathChange) -> ChangeDTO:
    """Translate one stored Change to the wire (§6).

    The summary is :func:`~aleph.services.tutor_context.change_summary_text` —
    the *same* line the shaper reads in its carried Change history, so a learner
    comparing the sheet with what the tutor says about their path is comparing
    one sentence rather than two accounts of it. ``kinds`` is derived from the
    payload by the service, for the reason its docstring gives.
    """
    return ChangeDTO(
        id=change.id,
        summary=change_summary_text(change),
        kinds=change_kinds(change),
        status=change.status,
        applied_at=change.applied_at,
        undone_at=change.undone_at,
    )


def _message_dto(
    message: Message, *, resolutions: Mapping[uuid.UUID, ProposalResolution]
) -> ShapingMessageDTO:
    """Translate one stored shaping message to the wire (§6).

    No lesson fields: a shaping turn is about the path as a whole, so the row
    carries no ``lesson_id`` and the DTO has nowhere to put one.

    ``proposal`` is validated (rather than constructed) on the way out, for
    ``routers/v1/tutor``'s reason: the payload was written by the agent tool, so
    this is where a shape that predates a field picks up its default. A message
    whose resolution is missing from ``resolutions`` reads as *pending*, the
    honest default — the derivation only omits rows that carry no Proposal.

    ``is None`` rather than falsiness: the column is *absent or a whole payload*
    (the service dumps a validated :class:`ProposalPayloadDTO` or writes ``NULL``
    — there is no third state), and an empty object arriving here would be a
    corrupt row that should fail loudly in validation, not read as "no Proposal".
    """
    payload = message.proposal
    proposal = (
        None
        if payload is None
        else ProposalDTO.model_validate(
            {**payload, "resolution": resolutions.get(message.id, "pending")}
        )
    )
    return ShapingMessageDTO(
        id=message.id,
        role=message.role,
        content=message.content,
        proposal=proposal,
        created_at=message.created_at,
    )


async def _live_path_state(
    session: AsyncSession, *, path: Path
) -> tuple[Sequence[ShapingDigestEntry], ShapingCaps]:
    """The ``digest`` and ``caps`` the *superseded* derivation re-validates against.

    The same three reads the context seam does for a turn, through the same
    builders — a Proposal is stale exactly when the predicates that drafted it no
    longer accept it against **live** state (D5), so this route must ask the
    question with the same inputs the next turn (and, later, apply) will use.
    Pure reads: reading a thread must no more start generating lessons than
    asking a question does, which is why the digest is built here rather than
    through the poll-as-trigger Phase 1 read seams.
    """
    lessons = await LessonRepository(session).list_for_path_with_engagement(path.id)
    unit_titles = {
        unit.id: unit.title
        for unit in await UnitRepository(session).list_for_path(path.id)
    }
    answers = await AttemptRepository(session).list_answers_for_path(path.id)
    return (
        build_shaping_digest(lessons, unit_titles=unit_titles, answers=answers),
        build_shaping_caps(lessons),
    )
