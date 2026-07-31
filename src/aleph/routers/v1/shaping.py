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

**Shaping writes no path structure here** (TDD §3). This module's routes reach
conversation rows and nothing else: no unit, no lesson, no attempt, no progress.
Apply and Undo — the only writes into path structure outside Phase 1's generation
pipeline — are AL-321's, land on this same router behind this same gate, and take
a per-path lock of their own (D11). That the conversation surface cannot mutate a
path is a property of what it imports, not a convention.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

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
    validate_model_override,
)

# 2A's router-level helpers, imported rather than copied: ``ReservedStream`` for
# the reason its own docstring gives, and ``_not_found`` because two spellings of
# "a 404 through the shared envelope" is how one rail starts disclosing existence
# the other hides. W21 freezes ``routers/v1/tutor.py`` this phase, so promoting
# the latter to a public name is a mechanical follow-up, not a reason to fork it.
from aleph.routers.v1.tutor import ReservedStream, _not_found
from aleph.services.feature_flags import FeatureFlag, FeatureFlagService
from aleph.services.shaping import shaping_turn_service
from aleph.services.sse import SSE_HEADERS, SSE_MEDIA_TYPE
from aleph.services.tutor_context import (
    build_shaping_caps,
    build_shaping_digest,
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
    from aleph.models import Message, Path

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
