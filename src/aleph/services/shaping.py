"""The shaping turn service: one learner ask in, one streamed reply out (AL-320).

Phase 2B TDD §5.5 in code, and it is Phase 2A's §5.5 with three substitutions —
the shaper agent instead of the tutor, shaping context instead of lesson context,
and a **Proposal** where a Tutor check would ride. The lifecycle itself is not
re-decided here; ``services/tutor.py`` is the pattern and its module docstring is
the long-form reasoning for every structural choice repeated below (the producer
task and its queue, why nothing is persisted on disconnect, why the request's
session is released before the stream starts, why the conversation's reservation
is released by the *response object* rather than by the generator).

**FOLLOW-UP (post-2B): extract the shared turn-lifecycle engine.** ``stream`` /
``_produce`` / ``_run_reply`` / ``_settle`` below are ~200 lines that differ from
``services/tutor.py``'s by their agent, their context builder and which tool
payload rides along; the duplication is deliberate *only* because W21 freezes
``services/tutor.py`` for this phase, so the alternative was editing 2A to
accommodate 2B. Once the freeze lifts, both rails should run one parameterised
engine (agent + context + payload observer in, SSE frames + a settled turn out),
and ``_text_of`` — imported privately below — should be promoted to a public name
on that engine rather than reached for across a module boundary.

**FOLLOW-UP (post-2B): split this module.** With Apply/Undo (AL-321) it is past
the 1000-line rubric line, and the seam is already visible — the turn lifecycle
above the Apply/Undo banner, the structural writers below it, sharing only the
conflict helpers. It is deliberately *not* split inside this phase: the TDD §3
names this single module as the one write path into path structure, and moving
the code mid-phase would churn the four AL-340 stamp points marked below for no
behavioural gain. Once the phase closes, ``services/shaping_changes.py`` (or
similar) should take everything under that banner.

These two are the pieces of this module that are known-wrong-on-purpose; nothing
else here is waiting on a follow-up.

What is genuinely this module's:

1. **The path must be ``ready``** (PRD §5.1, server-enforced): there is no
   structure to shape until the outline exists, so a send against any other
   status is a pre-stream ``409``. The rail hides its entry on such a path; this
   is the rule, that is the convenience.
2. **Its own conversation, its own lock, the shared pool** (D11). The thread is
   resolved lazily by ``(path_id, kind='shaping')`` — a second thread on the same
   path, never the in-lesson one (W21) — and gets its own one-in-flight
   reservation, so a shaping reply and an in-lesson reply may run at once on one
   path. They share the tutor's semaphore and its timeout, because a learner
   waiting mid-sentence is one workload class and splitting the pool would only
   let one rail starve the other.
3. **The Proposal, observed from the event stream** (D4 — 2A's D5 pattern
   exactly). ``propose_path_edit`` is a no-op tool; the *service* sees the call
   land, validates the payload once into :class:`ProposalPayloadDTO`, emits the
   new ``proposal`` SSE event and persists the same object on the tutor message
   row. Observing the tool **result** rather than the call is what excludes a
   payload the agent's validator rejected: a ``ModelRetry``'d call proposed
   nothing, so nothing is delivered and nothing is stored.

**Persisting a Proposal is not applying one.** This module writes conversation
rows and nothing else — no unit, no lesson, no attempt, no progress. That is a
property of what it imports, not a convention: there is no code path from here
into ``units``/``lessons``, and Apply/Undo (AL-321) are a separate surface with
their own lock and their own consent (the learner's tap). A **declined edit** is
likewise an ordinary persisted turn, distinguished only by its wording — the same
D5-cut posture 2A takes for a refusal, and the same additive path back if a
machine tag is ever wanted.

Layering: ``routers -> services -> (agents, repositories)``. This module binds
the model (agents never do), reads config, and owns the unit of work; the router
above it does auth, ownership, the picker gate and the response object.

Product events (TDD §9 — ``shaping_conversation_started``,
``shaping_message_sent``, ``shaping_reply_completed``, ``proposal_shown``) are
**AL-340's**, and the four points they are stamped from are marked below so that
ticket adds emitters rather than re-deriving a lifecycle.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import HTTPException, status
from pydantic_ai.exceptions import ModelAPIError, UnexpectedModelBehavior
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ToolReturnPart,
)
from pydantic_ai.run import AgentRunResultEvent
from sqlalchemy.exc import IntegrityError

from aleph.agents.shaper import (
    PROPOSE_PATH_EDIT_TOOL_NAME,
    AddLessonsOperation,
    ReviseLessonOperation,
    build_shaper_agent,
    insertions_after_first_shapeable,
    operations_within_caps,
    proposal_violation,
    revision_targets_unengaged,
    titles_nonempty_distinct,
)
from aleph.config import settings as global_settings
from aleph.db import new_session
from aleph.domains.changes import (
    ChangeInverse,
    PositionShift,
    QuickCheckSnapshot,
    RevisionSnapshot,
    UnitSlot,
    change_payload,
    plan_insertion_shifts,
    reverse_shifts,
)
from aleph.domains.engagement import LessonEngagement, is_engaged
from aleph.dtos.shaping import ProposalPayloadDTO, ShapingConflictReason
from aleph.dtos.tutor import (
    MessageDeltaDTO,
    MessageDoneDTO,
    TutorErrorCode,
)
from aleph.models import (
    ConversationKind,
    LessonGenerationState,
    Message,
    PathChangeKind,
    PathChangeStatus,
    PathStatus,
)
from aleph.repositories import (
    AttemptRepository,
    ChangeRepository,
    ConversationRepository,
    LessonRepository,
    QuickCheckRepository,
    UnitRepository,
)
from aleph.services.lifecycle import ConversationBusyError, TutorReplyLimiter
from aleph.services.openrouter import resolve_model
from aleph.services.rate_limit import build_daily_rate_limiter
from aleph.services.sse import sse_event, sse_heartbeat

# The 2A lifecycle's shared pieces, imported rather than copied. ``TutorReplyError``
# carries the learner-facing failure copy for a reply that did not land, and its
# wording is right on both rails — a shaping reply that times out kept nothing
# either, and PRD §5.7's rule (never blame the reader's connection, always say
# nothing was saved) is one rule, not two. ``_text_of`` answers "which stream
# event carries text?", a question about pydantic-ai and about neither rail, so
# a second copy of it is strictly worse than reaching across for the one that
# exists. Reaching for a *private* name is the part that is wrong: W21 freezes
# ``services/tutor.py`` this phase, so promoting it is part of the post-2B
# lifecycle extraction the module docstring records ("FOLLOW-UP (post-2B)").
from aleph.services.tutor import TutorReplyError, _text_of, tutor_turn_service
from aleph.services.tutor_context import (
    CHANGE_OPERATIONS_KEY,
    CHANGE_SUMMARY_KEY,
    assemble_shaping_context,
    build_shaping_caps,
    build_shaping_digest,
    parse_proposal_operations,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Mapping, Sequence
    from contextlib import AbstractAsyncContextManager

    from pydantic_ai.models import Model
    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.agents.shaper import (
        ShaperDeps,
        ShapingCaps,
        ShapingDigestEntry,
        ShapingOperation,
    )
    from aleph.config import Settings
    from aleph.models import Lesson, MessageSource, Path, PathChange, Unit
    from aleph.services.lifecycle import ReplyReservation
    from aleph.services.tutor_context import AssembledContext

    SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
    ResolveModel = Callable[[str], Model]

logger = structlog.get_logger(__name__)

# The wire name of the one new event (§5.4). A constant because three places
# agree on it — this module emits it, the DTO documents it, and the rail's
# stream parser (AL-330) matches it.
PROPOSAL_EVENT = "proposal"


class _EventTranslationError(RuntimeError):
    """A failure in *this module's* event translation, not the model's turn.

    Deliberately **not** a :class:`~aleph.services.tutor.TutorReplyError`, for
    the reason ``services/tutor.py``'s twin documents: it carries no
    learner-facing code, so it falls into :meth:`ShapingTurnService._produce`'s
    ``Exception`` clause, which logs a traceback (the chained ``__cause__`` is
    the real bug) and reports ``internal_error``. Filing a DTO/schema drift as
    ``upstream_error`` would leave it invisible in the logs and blame the
    provider.
    """


@dataclass(frozen=True)
class AdmittedShapingTurn:
    """A shaping turn that passed every pre-stream gate and holds its thread.

    Everything :meth:`ShapingTurnService.stream` needs and nothing bound to a
    database session: ``context`` is plain dataclasses and pydantic-ai messages,
    so the stream holds no pooled connection while it waits on the model.

    There is no ``lesson_id`` and no ``position_in_path`` — a shaping turn is
    about the path as a whole (PRD §5.1), which is the whole reason it is a
    second thread.

    2A's twin also carries ``account_id`` for its product events; this one does
    **not**, because AL-340 has not landed and a field with no reader is the same
    dead bookkeeping :meth:`ShapingTurnService._produce` refuses to plumb ahead
    of that ticket. AL-340 adds it back here and at the single construction site
    in :meth:`ShapingTurnService.admit`, where ``path.user_id`` is already in
    hand — one field and one line, and until then nothing claims to be carried
    for a reader that does not exist.
    """

    path_id: uuid.UUID
    content: str
    source: MessageSource
    model_id: str
    context: AssembledContext[ShaperDeps]
    reservation: ReplyReservation


@dataclass
class _ReplyResult:
    """What one completed shaping run produced, before it is persisted."""

    text: str
    proposal: dict[str, Any] | None


class ShapingTurnService:
    """Runs the §5.5 lifecycle for one shaping turn at a time, per request.

    Seams are constructor-injected in the Phase 1/2A style so tests drive the
    whole endpoint deterministically: ``_resolve_model`` (patched to a
    ``FunctionModel`` for streamed stubs and failure injection),
    ``_session_factory`` (the settle transaction), ``_replies`` (the D11 bounds)
    and ``_config``.

    ``replies`` defaults to a limiter **sharing the tutor's semaphore** (D11):
    one pool for both interactive reply kinds, a separate one-in-flight
    reservation per rail. Passing an explicit limiter is what test suites do, so
    each keeps its own in-flight registry and its own semaphore.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory = new_session,
        resolve_model_fn: ResolveModel = resolve_model,
        config: Settings = global_settings,
        replies: TutorReplyLimiter | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._resolve_model = resolve_model_fn
        self._config = config
        self._replies = replies or TutorReplyLimiter(
            semaphore=tutor_turn_service.replies.slot()
        )

    @property
    def replies(self) -> TutorReplyLimiter:
        """This service's bounds — the shared semaphore, its own reservations."""
        return self._replies

    # -- 1. admission (everything that can still be an ordinary error) ------- #

    async def admit(
        self,
        *,
        path: Path,
        is_admin: bool,
        content: str,
        source: MessageSource,
        model_id: str,
    ) -> AdmittedShapingTurn:
        """Run §5.5's pre-stream gates, or raise the ``HTTPException`` the route
        returns.

        ``path`` is the owned row the router already resolved; the reads here
        open their **own** short-lived session so the request's session is free
        for the duration of the stream.

        Order is load-bearing, and it is 2A's with the target check replaced:
        the path's status is validated before the conversation is claimed (a
        path with no structure must not lock its thread), the claim comes before
        the cap check (so a burst is refused as a conflict rather than eating
        quota), and the reservation is released on *any* failure after it —
        otherwise a rejected send would wedge the conversation until the process
        restarted.

        On success the caller owns the returned turn's ``reservation`` and
        **must** hand it back to :meth:`release`; the route does that from the
        response object's ``finally``.
        """
        _require_ready(path)
        async with self._session_factory() as session:
            try:
                reservation = self._replies.reserve(path.id)
            except ConversationBusyError as exc:
                raise _conflict(
                    "a reply is already in flight on this shaping conversation"
                ) from exc
            try:
                limiter = build_daily_rate_limiter(session)
                await limiter.check_shaping_message(
                    user_id=path.user_id, is_admin=is_admin
                )
                context = await assemble_shaping_context(session, path=path)
            except BaseException:
                self._replies.release(reservation)
                raise

        # AL-340: ``shaping_message_sent`` is stamped here — admission, not
        # persistence, is when the learner's ask exists (2A's argument: a reply
        # that later fails is our failure, not an un-asked question, and D2
        # would otherwise erase it from the adoption metric entirely). It is also
        # where ``account_id=path.user_id`` joins the turn — see
        # :class:`AdmittedShapingTurn` for why it is not carried yet.
        return AdmittedShapingTurn(
            path_id=path.id,
            content=content,
            source=source,
            model_id=model_id,
            context=context,
            reservation=reservation,
        )

    def release(self, turn: AdmittedShapingTurn) -> None:
        """Free the conversation ``turn`` claimed at admission. Idempotent.

        Called from the one frame ASGI guarantees will run once a response
        object exists — ``ReservedStream.__call__``'s ``finally`` in the router
        — rather than from the response generator, which may never be started at
        all (see :meth:`stream`).
        """
        self._replies.release(turn.reservation)

    # -- 2/3. the stream (the only place SSE is spoken) --------------------- #

    async def stream(self, turn: AdmittedShapingTurn) -> AsyncIterator[str]:
        """Yield the turn's SSE frames until a terminal ``done`` or ``error``.

        Structurally identical to ``services/tutor.py``'s, including why: the
        producer owns the model, the transaction and the error mapping; this
        consumer owns the socket and fills every ``SSE_HEARTBEAT_SECONDS`` of
        silence with a ``: ping`` so no proxy idle-timeout kills a stream that is
        merely waiting on a slow first token.

        The ``finally`` cancels the producer and **does not release the
        reservation** — an async generator that is never started never runs its
        ``finally`` (PEP 525), and Starlette can create this response and cancel
        it before the first ``__anext__``. The release therefore belongs to the
        response object wrapping this generator.
        """
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        producer = asyncio.create_task(self._produce(turn, queue))
        heartbeat = self._config.sse_heartbeat_seconds
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=heartbeat)
                except TimeoutError:
                    yield sse_heartbeat()
                    continue
                if frame is None:
                    return
                yield frame
        finally:
            producer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await producer

    async def _produce(
        self, turn: AdmittedShapingTurn, queue: asyncio.Queue[str | None]
    ) -> None:
        """Run the reply and push its frames; ``None`` closes the stream.

        Every exit path pushes exactly one terminal frame (``done`` or ``error``)
        and then the sentinel, so the consumer never has to reason about how the
        reply ended. The three outcomes are 2A's: ``success`` (a settled turn — a
        **declined edit** included, since an out-of-vocabulary ask answered
        gracefully is a real turn and is not machine-tagged), ``failure`` (any
        error, upstream or ours), and ``stopped``.

        ``stopped`` is a :class:`asyncio.CancelledError`, which is what a learner
        ending their own turn looks like from here: the consumer's ``finally``
        cancels this task when Starlette closes the generator, and the rail's
        stop affordance and a plain disconnect are the same event on the socket.
        It passes straight through — ``CancelledError`` is a ``BaseException``,
        so the ``Exception`` clause below cannot swallow it, and cancellation is
        not ours to swallow anyway. It is deliberately **not** a failure: filing
        learner behaviour as one would put it in the reply-failure guardrail,
        which is the distinction AL-340's ``outcome`` field has to preserve.
        """
        try:
            async with self._replies.slot():
                # The permit bounds the model run only; the timeout is inside it
                # so queue time is not charged against the reply's budget.
                reply = await self._run_reply(turn, queue)
            learner_id, tutor_id = await self._settle(turn, reply)
            await queue.put(
                sse_event(
                    "done",
                    MessageDoneDTO(
                        learner_message_id=learner_id, tutor_message_id=tutor_id
                    ),
                )
            )
        except TutorReplyError as exc:
            await queue.put(exc.frame)
        except Exception:
            logger.exception("shaping_reply_unhandled_error", path_id=str(turn.path_id))
            await queue.put(TutorReplyError(TutorErrorCode.INTERNAL_ERROR).frame)
        finally:
            # AL-340: ``shaping_reply_completed`` is stamped here, on every
            # resolution — which clause was taken *is* its ``outcome``
            # (success / failure / stopped), and the latency and token figures
            # §9 asks for are measured around this block. Deliberately not
            # plumbed ahead of that ticket: bookkeeping with no reader is how a
            # measurement quietly stops being measured.
            await queue.put(None)

    async def _run_reply(
        self,
        turn: AdmittedShapingTurn,
        queue: asyncio.Queue[str | None],
    ) -> _ReplyResult:
        """Stream one shaper run, translating its events to SSE frames.

        Bounded by ``TUTOR_REPLY_TIMEOUT`` (shared, D11) so a hung provider ends
        in ``error`` and never in a dead stream. Everything that goes wrong *on
        the model's side* is the model's turn failing and is reported as
        ``upstream_error`` — including a run that exhausts the agent's shared
        ``retries`` budget (``UnexpectedModelBehavior``), which is §5.8's "the
        proposal arguments stayed invalid" case seen from here.

        The ``upstream_error`` catch-all is scoped to exactly that: resolving the
        model and pulling the next event off the run. Translating an event —
        validating an observed payload against :class:`ProposalPayloadDTO`,
        encoding a frame — is *this module's* code, so a failure there is a bug
        of ours and is wrapped in :class:`_EventTranslationError`.
        """
        agent = build_shaper_agent()
        text: str | None = None
        proposal: dict[str, Any] | None = None
        # Tool calls observed but not yet accepted, by call id. A Proposal is
        # only delivered once its arguments have passed the agent's validator —
        # a ``ModelRetry``'d call proposed nothing.
        proposed: dict[str, dict[str, Any]] = {}

        try:
            # Resolving the model is inside the ``try`` deliberately: a provider
            # that cannot even be constructed (a deployment missing its API key)
            # is an upstream failure, and the learner should be told the tutor is
            # unavailable rather than shown "something went wrong on our side".
            model = self._resolve_model(turn.model_id)
            async with (
                asyncio.timeout(self._config.tutor_reply_timeout),
                agent.run_stream_events(
                    turn.content,
                    deps=turn.context.deps,
                    message_history=turn.context.message_history,
                    model=model,
                ) as run_events,
            ):
                async for event in run_events:
                    try:
                        delta = _text_of(event)
                        if delta:
                            await queue.put(
                                sse_event("delta", MessageDeltaDTO(text=delta))
                            )
                        elif isinstance(event, FunctionToolCallEvent):
                            _record_call(proposed, event)
                        elif isinstance(event, FunctionToolResultEvent):
                            payload = _accepted_proposal(proposed, event)
                            if payload is not None and proposal is None:
                                # One shape for the wire and the row: the card
                                # the rail draws from this event is the same
                                # object a later thread read returns, so nothing
                                # has to be re-derived to re-render it.
                                card = ProposalPayloadDTO.model_validate(payload)
                                proposal = card.model_dump(mode="json")
                                await queue.put(sse_event(PROPOSAL_EVENT, card))
                                # AL-340: ``proposal_shown`` is stamped here —
                                # where the card reaches the rail, which is what
                                # "shown" means. A call the validator rejected
                                # never gets here.
                        elif isinstance(event, AgentRunResultEvent):
                            text = str(event.result.output)
                    except Exception as exc:
                        # Ours, not the model's — see the method docstring.
                        raise _EventTranslationError from exc
        except TimeoutError as exc:
            raise TutorReplyError(TutorErrorCode.TIMEOUT) from exc
        except (UnexpectedModelBehavior, ModelAPIError) as exc:
            logger.warning("shaping_reply_model_failed", error=repr(exc))
            raise TutorReplyError(TutorErrorCode.UPSTREAM_ERROR) from exc
        except _EventTranslationError:
            raise
        except Exception as exc:
            logger.warning("shaping_reply_stream_failed", error=repr(exc))
            raise TutorReplyError(TutorErrorCode.UPSTREAM_ERROR) from exc

        if text is None:  # pragma: no cover - a completed run always has output
            raise TutorReplyError(TutorErrorCode.UPSTREAM_ERROR)
        return _ReplyResult(text=text, proposal=proposal)

    async def _settle(
        self, turn: AdmittedShapingTurn, reply: _ReplyResult
    ) -> tuple[uuid.UUID, uuid.UUID]:
        """One transaction: the shaping conversation (if new) and the whole turn.

        The ids are read *before* the commit — ``insert_turn`` flushes, so they
        exist — because the session closes on the way out of this block.

        ``lesson_id=None`` is the shaping thread's shape (migration ``0006``):
        the turn is about the path, so there is no lesson it was asked in.

        An ``IntegrityError`` here means the position pair collided, i.e. the
        per-conversation reservation was somehow bypassed. That is a failed
        reply, not a 500 with half a turn on the wire: the transaction rolls back
        with the session, and the learner is told the reply did not land.
        """
        try:
            async with self._session_factory() as session:
                repository = ConversationRepository(session)
                conversation, created = await repository.upsert_for_path(
                    turn.path_id, kind=ConversationKind.SHAPING
                )
                learner, tutor = await repository.insert_turn(
                    conversation_id=conversation.id,
                    lesson_id=None,
                    learner_content=turn.content,
                    source=turn.source,
                    tutor_content=reply.text,
                    proposal=reply.proposal,
                )
                ids = (learner.id, tutor.id)
                await session.commit()
            # AL-340: ``shaping_conversation_started`` is stamped here when the
            # upsert reports ``created`` — after the commit, so the event never
            # claims a thread a rolled-back transaction did not leave behind,
            # and exactly once per path rather than once per first-turn-shaped
            # request (which is the whole reason the upsert returns the flag).
            _ = created
            return ids
        except IntegrityError as exc:
            logger.warning("shaping_turn_insert_conflicted", error=repr(exc))
            raise TutorReplyError(TutorErrorCode.INTERNAL_ERROR) from exc


def _record_call(
    proposed: dict[str, dict[str, Any]], event: FunctionToolCallEvent
) -> None:
    """Remember a ``propose_path_edit`` call's arguments until it is accepted."""
    part = event.part
    if part.tool_name != PROPOSE_PATH_EDIT_TOOL_NAME or part.tool_call_id is None:
        return
    try:
        arguments = part.args_as_dict()
    except (ValueError, TypeError, json.JSONDecodeError):
        # Malformed JSON arguments: pydantic-ai will feed the model a retry, and
        # there is nothing here worth delivering to the learner.
        return
    proposed[part.tool_call_id] = arguments


def _accepted_proposal(
    proposed: dict[str, dict[str, Any]], event: FunctionToolResultEvent
) -> dict[str, Any] | None:
    """The Proposal payload a *successful* tool return corresponds to, if any.

    A ``RetryPromptPart`` result means the arguments were rejected (the shared D1
    predicates raised ``ModelRetry`` through ``validate_proposal``) — the model
    proposed nothing, so nothing is delivered and nothing is persisted. Observing
    the *result* rather than the call is what makes that distinction, and it is
    still mid-stream: the frame may land before, between, or after the reply's
    own deltas, so the client attaches it to the message rather than to a
    position in the text.
    """
    part = event.part
    if (
        not isinstance(part, ToolReturnPart)
        or part.tool_name != PROPOSE_PATH_EDIT_TOOL_NAME
    ):
        return None
    return proposed.pop(part.tool_call_id, None)


def _require_ready(path: Path) -> None:
    """``409`` unless ``path`` is ``ready`` — there is nothing else to shape.

    PRD §5.1's rule, enforced server-side: a path still generating its outline,
    or one that failed or was refused, has no structure a Proposal could name.
    ``409`` rather than ``404`` for Phase 1's "not generated yet" reason — the
    path exists and is the caller's, the request conflicts with its state.
    """
    if path.status is not PathStatus.READY:
        raise _conflict("this path is not ready to shape yet")


def _conflict(detail: str) -> HTTPException:
    """A ``409`` the shared envelope renders with code ``conflict``."""
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


# The module-level singleton the router uses, mirroring ``tutor_turn_service``:
# one object per process, so the in-flight reservations are genuinely
# process-wide and the semaphore is genuinely the tutor's (D11). Tests patch its
# private seams in place.
shaping_turn_service = ShapingTurnService()


# --------------------------------------------------------------------------- #
# Apply & Undo (AL-321) — the phase's correctness heart
# --------------------------------------------------------------------------- #
#
# Everything above this line writes conversation rows. Everything below it is
# the ONLY code in the application that writes ``units``/``lessons`` outside
# Phase 1's generation pipeline (TDD §3), and it does so only inside
# :meth:`ShapingChangeService.apply_change` / :meth:`ShapingChangeService.undo_change`.
# That is the structural form of the PRD's contract — *the only write path into
# path structure is Apply on a validated Proposal* — and it is a property of
# module topology rather than a convention: nothing else imports the writers.
#
# Three rules govern this whole section.
#
# 1. **The tap is the consent.** A stored Proposal is an offer. Only an explicit
#    ``POST .../apply-proposal`` turns it into structure, and only the payload
#    that was validated and persisted — never conversation text, never a "yes,
#    do that" in the composer.
# 2. **Apply-time truth is the only truth** (D5). A Proposal validated at draft
#    time can be stale by the time it is tapped: the learner attempted the
#    revision target, another Change landed, the path reached its cap. So the
#    whole payload is re-validated against **live** state, through the *same*
#    exported predicates the shaper drafted it with — never a second rulebook.
# 3. **Whole or not at all.** Each of apply and undo is one transaction under a
#    per-path lock (D11). The path is never half-changed (PRD §5.7), and the
#    Change row carries its own inverse, so undo needs no second source of truth
#    (D8).
#
# Progress is never written here. Not an Attempt, not a ``completed_at``, in
# either direction — undo removes only what the Change created, and by the
# engagement boundary (D2) anything the learner has worked on cannot be undone
# at all, so undo *cannot* reach progress even in principle (PRD §5.5).


# Units are renumbered through a scratch range because ``UNIQUE (path_id,
# position)`` is non-deferrable: moving unit 2 to 3 while 3 still exists raises,
# whatever order the moves are made in (unlike lessons, whose renumbering is a
# pure shift and therefore has a safe direction — D6). Parking every unit out of
# the way first and then assigning the final positions is the smallest thing that
# always works.
#
# Two disjoint scratch ranges, and the disjointness is the point. A new unit is
# *born* somewhere (it needs a position at insert, before the renumbering knows
# where it belongs), so it is born far above any real position; the parking pass
# then has to move it too, and would collide with itself if it parked into the
# same range. Negative positions can never be a real one (the outline numbers
# from 1) nor a birth one, so the two passes can never meet.
_NEW_UNIT_POSITION = 100_000

# Migration ``0007``'s partial unique index: one **applied** Change per proposal
# message, enforced by the database because the apply lock is process-local.
# Matched by name in the driver's message rather than by code, because asyncpg
# reports every unique violation with the same SQLSTATE and only the index name
# distinguishes "the same proposal twice" from a genuine bug.
_APPLIED_MESSAGE_INDEX = "uq_path_changes_applied_message"


class PathApplyLock:
    """One in-process lock per path, held across Apply and Undo (D11).

    **A different lock for a different resource.** The reply limiter above
    guards a *conversation* against two replies at once; this guards a *path*
    against two structural mutations at once. They do not overlap: a learner may
    be mid-reply on the shaping rail while an earlier Proposal is being applied,
    and neither should make the other wait.

    It **waits** rather than refusing, and that is the whole design of
    "concurrent applies — one wins". The loser does not get a lock error; it
    proceeds once the winner has committed and then re-validates against a path
    that has moved (D5) — so a double tap on one card gets a
    ``409 already_applied``, and two different Proposals that genuinely conflict
    get the specific staleness reason for *why*. A refusing lock would report
    "busy" for all of those, which is a worse answer to every one of them.

    **Scope: one process**, exactly as the reply limiter's reservations are —
    and a rolling deploy briefly runs two, so the rule that must not depend on
    it does not: migration ``0007``'s partial unique index on
    ``path_changes(message_id) WHERE status='applied'`` is what makes "a
    Proposal is applied at most once" true across machines, and
    :meth:`ShapingChangeService.apply_change` maps its violation to the same
    ``409 already_applied`` this lock's loser gets from the pre-check. What is
    still process-local is *serialisation* — two applies of **different**
    Proposals on one path — where the database's own backstop is
    ``UNIQUE (path_id, position_in_path)`` (a genuinely interleaved apply fails
    loudly rather than corrupting an order) and the escalation the TDD already
    names is ``SELECT … FOR UPDATE`` on the path's lessons inside the
    transaction, compatible with this lock rather than a replacement for it.
    """

    def __init__(self) -> None:
        # path_id -> (lock, how many coroutines are holding or waiting for it).
        # The count is what lets the map stay the size of the *contended* set
        # rather than growing by one entry per path ever applied to in this
        # process; dropping the entry only when the last waiter leaves is what
        # stops a fresh lock being handed out while someone still holds the old.
        self._locks: dict[uuid.UUID, tuple[asyncio.Lock, int]] = {}

    @contextlib.asynccontextmanager
    async def hold(self, path_id: uuid.UUID) -> AsyncIterator[None]:
        """Hold ``path_id``'s lock for the block, waiting for it if need be."""
        lock, waiting = self._locks.get(path_id, (asyncio.Lock(), 0))
        self._locks[path_id] = (lock, waiting + 1)
        try:
            async with lock:
                yield
        finally:
            held, waiting = self._locks[path_id]
            if waiting <= 1:
                del self._locks[path_id]
            else:
                self._locks[path_id] = (held, waiting - 1)


@dataclass(frozen=True)
class _LivePath:
    """The path as it is **right now** — the only state D5 lets apply trust."""

    lessons: list[tuple[Lesson, bool]]
    units: list[Unit]
    digest: tuple[ShapingDigestEntry, ...]
    caps: ShapingCaps

    def lesson(self, lesson_id: str) -> Lesson | None:
        for lesson, _engaged in self.lessons:
            if str(lesson.id) == lesson_id:
                return lesson
        return None


class ShapingChangeService:
    """Apply a validated Proposal, and undo the Change it made (§5.6/§5.7).

    Constructor-injected seams in the Phase 1/2A style: ``session_factory`` for
    the unit of work and ``locks`` so a test suite gets its own registry rather
    than sharing the process singleton's (and binding an ``asyncio.Lock`` to a
    dead event loop).

    Ownership is **not** this service's job — the router resolves it (message →
    conversation → path → account, or change → path → account) and passes ids
    in. Everything the service then reads, it reads again inside the lock, on
    purpose: the router's read happened before the lock and is therefore exactly
    the stale view D5 exists to distrust.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory = new_session,
        config: Settings = global_settings,
        locks: PathApplyLock | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._config = config
        self._locks = locks or PathApplyLock()

    @property
    def locks(self) -> PathApplyLock:
        """This service's per-path apply locks (D11)."""
        return self._locks

    # -- apply -------------------------------------------------------------- #

    async def apply_change(
        self, *, path_id: uuid.UUID, message_id: uuid.UUID
    ) -> uuid.UUID:
        """Turn the Proposal on ``message_id`` into a Change; return its id.

        §5.6 end to end, under the per-path lock and in one transaction:
        re-validate against live state, insert the ``path_changes`` row with its
        inverses, shift positions, insert the added rows ``ungenerated``,
        snapshot-and-reset the revised ones. Every stale case raises a
        ``409`` carrying a :class:`~aleph.dtos.shaping.ShapingConflictReason`
        the card can render.

        Generation is deliberately *not* awaited or triggered here: a Change is
        applied when structure lands, not when generation finishes (PRD §5.7).
        The router kicks the prefetch driver after the commit, which is also
        what refreshes the outline it returns.

        The ``IntegrityError`` branch is the **cross-process** half of "applied
        at most once": the per-path lock and the pre-check inside it are scoped
        to one process, and a Fly rolling deploy briefly runs two. Migration
        ``0007``'s partial unique index is what actually excludes the second
        write; this turns its failure into the same ``409 already_applied`` the
        in-process pre-check answers with, so a learner double-tapping across a
        deploy boundary cannot tell which guard caught them. Any *other*
        integrity failure is a bug and is re-raised untouched.
        """
        async with (
            self._locks.hold(path_id),
            self._session_factory() as session,
        ):
            try:
                change_id = await self._apply(
                    session, path_id=path_id, message_id=message_id
                )
                await session.commit()
            except IntegrityError as error:
                if _APPLIED_MESSAGE_INDEX not in str(error.orig):
                    raise
                raise _proposal_already_resolved(PathChangeStatus.APPLIED) from error
        # AL-340: ``change_applied`` is stamped here — after the commit, so the
        # event never claims structure a rolled-back transaction did not leave
        # behind. Its ``change_id`` is the return value; its kinds/counts come
        # off the stored payload, which is why they are not carried out of the
        # transaction ahead of a reader for them.
        return change_id

    async def _apply(
        self, session: AsyncSession, *, path_id: uuid.UUID, message_id: uuid.UUID
    ) -> uuid.UUID:
        """The apply transaction's body (the caller owns the commit)."""
        message = await session.get(Message, message_id)
        payload = None if message is None else message.proposal
        if message is None or not payload:
            # The router proved this a moment ago; re-proving it inside the lock
            # is what makes "a Proposal is applied at most once" true rather
            # than likely — a concurrent "new conversation" could have deleted
            # the row in between.
            raise _not_found("proposal not found")

        changes = ChangeRepository(session)
        resolved = await changes.resolution_of_message(message_id)
        if resolved is not None:
            raise _proposal_already_resolved(resolved)

        operations = parse_proposal_operations(payload)
        if operations is None:
            # A payload this app can no longer read cannot be re-validated, so
            # it fails closed — the same posture the *superseded* derivation
            # takes, and the only safe one when the question is "may this write
            # to the learner's path?".
            raise _stale(
                ShapingConflictReason.INVALID_PROPOSAL,
                "this proposal is in a shape this app no longer understands",
            )
        summary = payload.get(CHANGE_SUMMARY_KEY)
        summary = summary if isinstance(summary, str) else ""

        live = await _load_live_path(session, path_id)
        violation = proposal_violation(
            operations, summary=summary, digest=live.digest, caps=live.caps
        )
        if violation is not None:
            raise _stale(_stale_reason(operations, live), violation)
        _require_unshifted_positions(
            operations,
            await changes.list_for_path(path_id),
            proposed_at=message.created_at,
        )
        _require_targets_idle(operations, live)

        inverse = ChangeInverse()
        inverse = await _apply_revisions(session, operations, live, inverse)
        inverse = await _apply_additions(session, path_id, operations, live, inverse)

        change = await changes.create(
            path_id=path_id,
            message_id=message_id,
            kind=_change_kind(operations),
            payload=change_payload(
                operations=payload.get(CHANGE_OPERATIONS_KEY) or [],
                summary=summary,
                inverse=inverse,
            ),
        )
        return change.id

    # -- undo --------------------------------------------------------------- #

    async def undo_change(self, *, path_id: uuid.UUID, change_id: uuid.UUID) -> None:
        """Reverse a Change exactly, or refuse with a coded ``409`` (§5.7).

        The same lock and the same all-or-nothing transaction as apply. What is
        restored is exactly what the Change's payload records it did: added rows
        deleted, positions unshifted, revised lessons put back byte-identical
        (passage, Quick check, title, ``generated_at``, state ``generated``,
        instruction cleared). ``status`` flips to ``undone`` — undo is never a
        delete, because the history is the record of what *happened*.

        What is **not** restored, because it was never touched: progress. Undo
        removes only what the Change created, and the D2 re-check below means a
        Change whose content the learner has met cannot be undone at all — so
        no Attempt and no completion can ever be in reach of this code path
        (PRD §5.5).
        """
        async with (
            self._locks.hold(path_id),
            self._session_factory() as session,
        ):
            await self._undo(session, path_id=path_id, change_id=change_id)
            await session.commit()
        # AL-340: ``change_undone`` is stamped here, after the commit, with
        # ``minutes_since_apply`` from the row's ``applied_at``.

    async def _undo(
        self, session: AsyncSession, *, path_id: uuid.UUID, change_id: uuid.UUID
    ) -> None:
        """The undo transaction's body (the caller owns the commit)."""
        changes = ChangeRepository(session)
        change = await changes.get(change_id)
        if change is None:
            raise _not_found("change not found")
        if change.status is not PathChangeStatus.APPLIED:
            raise _conflict_reason(
                ShapingConflictReason.NOT_APPLIED,
                "this change has already been undone",
            )
        _require_newest_live(change, await changes.list_applied_for_path(path_id))

        inverse = ChangeInverse.from_payload(change.payload)
        lessons_live, units_live = await _load_path_structure(session, path_id)
        _require_undo_open(inverse, lessons_live)

        lessons = LessonRepository(session)
        # Order is the whole of D8's mechanics. Deleting first frees the slots
        # the added lessons occupy, so every unshift below lands on an empty
        # position; unshifting first would collide on the first row.
        #
        # No "skip the rows we just deleted" guard is needed: a shift plan never
        # names a lesson the *same* Change added. Each Addition's plan moves only
        # rows at or after its offset-adjusted insertion point, and an earlier
        # Addition's lessons all sit strictly below it (the running offset is
        # exactly their width), so they are never in a later plan.
        for lesson_id in inverse.added_lesson_ids:
            await lessons.delete(uuid.UUID(lesson_id))
        for shift in reverse_shifts(inverse.shifts):
            await lessons.move_to_position(
                lesson_id=uuid.UUID(shift.lesson_id),
                position_in_path=shift.to_position,
            )
        # Reverse chronological, exactly as the position unshift is: a Proposal
        # with two Additions moves a lesson's display slot **twice**, and
        # replaying the recorded moves forwards would leave the *earlier* value
        # as the last write. There are no collisions to dodge here
        # (``position_in_unit`` carries no unique constraint) — just
        # last-write-wins arithmetic that is only right in the inverse order.
        for slot in reversed(inverse.slots):
            await lessons.move_within_unit(
                lesson_id=uuid.UUID(slot.lesson_id),
                position_in_unit=slot.from_position,
            )
        for revision in inverse.revisions:
            await _restore_revision(session, revision)

        units = UnitRepository(session)
        for unit_id in inverse.added_unit_ids:
            await units.delete(uuid.UUID(unit_id))
        if inverse.units:
            restored = {unit_id: position for unit_id, position in inverse.units}
            await _renumber_units(
                session,
                {
                    unit.id: restored.get(str(unit.id), unit.position)
                    for unit in units_live
                    if str(unit.id) not in inverse.added_unit_ids
                },
            )

        if not await changes.mark_undone(change_id):  # pragma: no cover - lock-held
            raise _conflict_reason(
                ShapingConflictReason.NOT_APPLIED,
                "this change has already been undone",
            )


# The module-level singleton the router uses, for ``shaping_turn_service``'s
# reason: the per-path locks must be process-wide to be locks at all.
shaping_change_service = ShapingChangeService()


# --- live state ------------------------------------------------------------- #


async def _load_live_path(session: AsyncSession, path_id: uuid.UUID) -> _LivePath:
    """The three reads **apply** re-validates against (D5).

    Deliberately the **same** builders the context seam uses for a turn and the
    conversation read uses for the *superseded* derivation: a Proposal is stale
    exactly when the predicates that drafted it no longer accept it, so all
    three places must ask that question with identical inputs or the card and
    the server will disagree about whether Apply can work.
    """
    lessons, units = await _load_path_structure(session, path_id)
    unit_titles = {unit.id: unit.title for unit in units}
    answers = await AttemptRepository(session).list_answers_for_path(path_id)
    return _LivePath(
        lessons=lessons,
        units=units,
        digest=build_shaping_digest(lessons, unit_titles=unit_titles, answers=answers),
        caps=build_shaping_caps(lessons),
    )


async def _load_path_structure(
    session: AsyncSession, path_id: uuid.UUID
) -> tuple[list[tuple[Lesson, bool]], list[Unit]]:
    """The path's lessons (with engagement) and units — what **undo** reads.

    Undo re-validates against live state as apply does, but against a strictly
    smaller question: the D2 engagement boundary and the unit rows the inverse
    renumbers. It asks nothing the *digest* or the *caps* could answer — a
    Proposal's shape is not on trial, only whether this Change is still open —
    so it does not pay for the Attempt scan and the digest build that
    :func:`_load_live_path` runs.
    """
    return (
        await LessonRepository(session).list_for_path_with_engagement(path_id),
        await UnitRepository(session).list_for_path(path_id),
    )


# --- staleness (D5) --------------------------------------------------------- #


def _additions(
    operations: Sequence[ShapingOperation],
) -> list[AddLessonsOperation]:
    return [
        operation
        for operation in operations
        if isinstance(operation, AddLessonsOperation)
    ]


def _revisions(
    operations: Sequence[ShapingOperation],
) -> list[ReviseLessonOperation]:
    return [
        operation
        for operation in operations
        if isinstance(operation, ReviseLessonOperation)
    ]


def _change_kind(operations: Sequence[ShapingOperation]) -> PathChangeKind:
    """The Change row's ``kind`` for a Proposal that may carry both shapes.

    One Apply is one Change — the unit of history *and* of undo (CONTEXT.md) —
    so a Proposal mixing an **Addition** with a **Revision** lands as a single
    row, and the single ``kind`` column has to name something. It names the
    dominant shape: a Change that grows the path is an ``add_lessons`` Change
    even if it also re-teaches something, because growth is the part the learner
    will look for in their history. The full truth is in ``payload.operations``,
    which is what the history endpoint's ``kinds`` (plural) is derived from and
    what the TDD §6 line "kind(s)" asks for.
    """
    return (
        PathChangeKind.ADD_LESSONS
        if _additions(operations)
        else PathChangeKind.REVISE_LESSON
    )


def change_kinds(change: PathChange) -> list[PathChangeKind]:
    """The edit shape(s) a Change carries, for the history sheet (§6).

    Derived from the stored operations rather than read off the ``kind`` column,
    because one Apply is one Change even when the Proposal mixed an **Addition**
    with a **Revision** (see :func:`_change_kind`) and the learner's history
    should say so. The two shapes are discriminated **structurally** — an
    Addition carries ``lessons``, a Revision carries ``lesson_id`` — the same
    untagged discrimination ``agents/shaper.py`` documents for the payload union
    and ``services/stub_model.py`` builds against; a tag would have to arrive on
    both sides at once.

    Order follows first appearance in the payload, so the sheet reads in the
    order the Proposal stated. A payload that yields nothing (a row from an older
    shape) falls back to the row's own column rather than an empty list — the
    history is the learner's record, and a blank kind is worse than a coarse one.
    """
    kinds: list[PathChangeKind] = []
    for operation in (change.payload or {}).get(CHANGE_OPERATIONS_KEY) or []:
        if not isinstance(operation, dict):
            continue
        if "lessons" in operation:
            kind = PathChangeKind.ADD_LESSONS
        elif "lesson_id" in operation:
            kind = PathChangeKind.REVISE_LESSON
        else:
            continue
        if kind not in kinds:
            kinds.append(kind)
    return kinds or [change.kind]


def _stale_reason(
    operations: Sequence[ShapingOperation], live: _LivePath
) -> ShapingConflictReason:
    """*Which* rule the now-invalid Proposal broke, as a code the card renders.

    Called only once :func:`~aleph.agents.shaper.proposal_violation` has already
    said "no", and it re-asks the **exported predicates** rather than
    re-implementing any of them: the rulebook decides *whether* a Proposal is
    stale, and this only labels the answer. Keeping those two jobs apart is what
    stops a label drifting into a second, quieter validator — the order below
    mirrors ``proposal_violation``'s so the label always names the rule that
    actually fired first.
    """
    if not operations_within_caps(operations, caps=live.caps):
        return ShapingConflictReason.PATH_CAP_REACHED
    if not insertions_after_first_shapeable(
        operations, digest=live.digest, caps=live.caps
    ):
        return ShapingConflictReason.INSERT_POSITION_TAKEN
    if not revision_targets_unengaged(operations, digest=live.digest):
        return ShapingConflictReason.REVISION_TARGET_ENGAGED
    if not titles_nonempty_distinct(operations, digest=live.digest):
        return ShapingConflictReason.TITLE_CONFLICT
    return ShapingConflictReason.INVALID_PROPOSAL


def _require_unshifted_positions(
    operations: Sequence[ShapingOperation],
    history: Sequence[PathChange],
    *,
    proposed_at: datetime,
) -> None:
    """Refuse an Addition whose recorded positions no longer name their slot.

    D5's "re-resolve insertion positions", and the one apply-time check that is
    **not** one of the D1 predicates — deliberately, because it is a different
    question. The predicates ask *is this payload well formed against the path*;
    this asks *does its ``insert_at_position`` still mean what the learner was
    shown*. A payload can pass the first and fail the second: the learner sees
    "after lesson 3", another Change inserts two lessons at 2, and position 4
    now points at somebody else's lesson while still being perfectly in bounds.
    Applying it would put the lessons somewhere the learner never consented to,
    which is the one thing this phase must not do.

    **One rule, stated once:** *if any structural shift event has happened since
    this Proposal was made, at or below the last position the payload names,
    refuse.* The two halves each cost a bug when they are narrower than that.

    * **The bound is the payload's** ``max`` **insert point, not its** ``min``.
      One Proposal may carry several Additions in one coordinate frame, and a
      Change landing *between* two of them leaves the first meaning what the
      learner saw and the second meaning something else. Bounding on the
      earliest would wave the whole payload through on the strength of its
      safest operation.
    * **An Undo is a shift event too.** It moves every position at or after the
      undone Change's insert point *down* by that Change's size, exactly as the
      apply moved them up. Scanning only live Changes misses it precisely
      because the row that moved the path is the one no longer in force.

    A Change that was both applied **and** undone since ``proposed_at`` is net
    zero and is not an event — hence the exclusive-or below rather than two
    independent tests. Everything else about the rule is deliberately blunt: it
    compares recorded insert points from *different* proposal-time coordinate
    frames (:mod:`aleph.domains.changes` — the payload records positions, not
    anchors, and frames drift), so it over-refuses at the margin and never
    under-refuses. A false ``409`` costs an "ask again"; a false accept writes
    lessons into a slot nobody consented to.

    Revisions never move anything, so a path whose only later Change was a
    Revision is not shifted — that falls out of the insert-point scan rather
    than being a case here.

    The card reads ``positions_shifted`` and offers "ask again" — the honest
    affordance, since the shaper will re-draft against the path as it now is.
    """
    additions = _additions(operations)
    if not additions:
        return
    bound = max(addition.insert_at_position for addition in additions)
    for change in history:
        if not _shifted_since(change, proposed_at):
            continue
        if any(position <= bound for position in _insert_positions(change)):
            raise _stale(
                ShapingConflictReason.POSITIONS_SHIFTED,
                "another change has moved the lessons this proposal names. "
                "Ask again and the tutor will propose it against the path "
                "as it is now.",
            )


def _shifted_since(change: PathChange, proposed_at: datetime) -> bool:
    """Whether ``change`` moved this path's positions after ``proposed_at``.

    Applying moves positions one way and undoing moves them back, so a Change
    disturbs a Proposal's coordinate frame when **exactly one** of its two
    stamps falls after the Proposal was made. Both after (applied and undone
    since) is a round trip the payload never saw; neither after is a state the
    payload was already drafted against.
    """
    applied_since = change.applied_at > proposed_at
    undone_since = (
        change.status is PathChangeStatus.UNDONE
        and change.undone_at is not None
        and change.undone_at > proposed_at
    )
    return applied_since != undone_since


def _insert_positions(change: PathChange) -> list[int]:
    """Every ``insert_at_position`` a stored Change's operations recorded.

    The positions at or after which that Change moved the path — the same set
    whether it was being applied or undone, because an undo reverses those very
    moves. Read off the stored payload rather than the inverse's shifts: an
    Addition past the end of the path shifts nothing yet still names a position,
    and the payload is what the learner consented to.
    """
    positions: list[int] = []
    for operation in (change.payload or {}).get(CHANGE_OPERATIONS_KEY) or []:
        if not isinstance(operation, dict):
            continue
        inserted_at = operation.get("insert_at_position")
        if isinstance(inserted_at, int):
            positions.append(inserted_at)
    return positions


def _require_targets_idle(
    operations: Sequence[ShapingOperation], live: _LivePath
) -> None:
    """Refuse a Revision of a lesson that is generating right now (§5.6 step 2).

    Retryable and not stale: a prefetch holds the claim, nothing about the
    Proposal is wrong, and the same tap works in a moment. Resetting the row
    under a live claim would race the worker's fenced ``mark_generated`` — which
    would then land the *pre-revision* content on a row apply had just cleared.

    The raw generation state is used rather than the effective one: a stale
    ``generating`` row is re-claimable, and treating it as idle is what would
    let apply and a re-claim collide.
    """
    for revision in _revisions(operations):
        lesson = live.lesson(revision.lesson_id)
        if lesson is None:  # pragma: no cover - the predicates rejected it first
            continue
        if lesson.generation_state is LessonGenerationState.GENERATING:
            raise _conflict_reason(
                ShapingConflictReason.TARGET_GENERATING,
                "this lesson is being written right now — try again in a moment",
            )


def _require_newest_live(change: PathChange, applied: Sequence[PathChange]) -> None:
    """Undo is **last-in-first-out**: only the newest live Change may be undone.

    A Change's inverse is a list of *absolute* positions, recorded against the
    path as it stood when that Change was applied (D8 — recorded rather than
    recomputed, because a path that has moved on must not be re-derived from).
    Replaying those positions against a path a **later** Change has since moved
    is not merely imprecise, it is wrong in two ways, and the integration suite
    pins both: it can walk an existing lesson into a slot the later Change
    occupies (a ``UNIQUE (path_id, position_in_path)`` failure), and — worse,
    because it is silent — it can land the later Change's lesson on the far side
    of a lesson the learner had placed it before.

    Neither is fixable by being cleverer here. The recorded positions of two
    Changes live in two different coordinate frames, and nothing in the payload
    relates them (see :mod:`aleph.domains.changes`); a generally-correct
    interleaved undo would need the operations re-expressed against anchors
    (lesson ids) rather than positions, which is a payload redesign, not a
    guard. LIFO is the restriction under which the recorded frames *are* the
    live frame — the newest live Change was applied against exactly the path
    that is there now — and it costs the learner nothing PRD §5.5 promised: a
    Change stays undoable until it is engaged, by undoing the ones above it
    first. The sheet says which one that is.

    The rule is **uniform over kinds** rather than "only when a later Change
    moved positions". A pure Revision's inverse touches content keyed by lesson
    id and no position at all, so undoing an older one under a newer Addition
    would in fact be safe — but not under a newer *Revision* of the same lesson
    (the generation seam reads the newest live snapshot for the prompt, and
    restoring an older one behind it would teach the learner's lesson from a
    passage two Changes stale). One rule the sheet can state in a sentence beats
    a per-kind matrix that is right for a reason nobody can see.

    ``applied`` is the path's live Changes newest first
    (:meth:`~aleph.repositories.changes.ChangeRepository.list_applied_for_path`),
    read inside the lock like everything else undo trusts.
    """
    if applied and applied[0].id != change.id:
        raise _conflict_reason(
            ShapingConflictReason.NOT_LATEST,
            "a later change was made on top of this one — undo that one first, "
            "and this change can then be undone",
        )


def _require_undo_open(
    inverse: ChangeInverse, lessons: Sequence[tuple[Lesson, bool]]
) -> None:
    """The D2 re-check that decides whether undo is still open (§5.7 step 2).

    **This is the rule; the UI's disabled state is a convenience.** Engagement is
    derived, never stored, and derived here from the same
    :func:`~aleph.domains.engagement.is_engaged` predicate proposal validation
    and apply use — three call sites, one predicate, because a second spelling
    is how they start disagreeing.

    A lesson the Change created or revised that the learner has since attempted
    or completed closes the window for good: the Change becomes permanent
    history (PRD §5.5) and the card says so plainly. It also re-checks that no
    revision target is mid-generation, for :func:`_require_targets_idle`'s
    reason — restoring a snapshot under a live claim would let the worker's
    fenced write land on top of the restored row.
    """
    touched = set(inverse.added_lesson_ids) | {
        revision.lesson_id for revision in inverse.revisions
    }
    for lesson, has_attempt in lessons:
        if str(lesson.id) not in touched:
            continue
        if is_engaged(
            LessonEngagement(
                position_in_path=lesson.position_in_path,
                completed_at=lesson.completed_at,
                has_attempt=has_attempt,
            )
        ):
            raise _conflict_reason(
                ShapingConflictReason.ENGAGED,
                "you have already started one of the lessons this change made, "
                "so it is now part of your path's history",
            )
        if lesson.generation_state is LessonGenerationState.GENERATING and str(
            lesson.id
        ) in {revision.lesson_id for revision in inverse.revisions}:
            raise _conflict_reason(
                ShapingConflictReason.TARGET_GENERATING,
                "this lesson is being written right now — try again in a moment",
            )


# --- the mutations (the only writes into path structure outside Phase 1) ----- #


async def _apply_revisions(
    session: AsyncSession,
    operations: Sequence[ShapingOperation],
    live: _LivePath,
    inverse: ChangeInverse,
) -> ChangeInverse:
    """Snapshot each Revision's lesson, then reset it to ``ungenerated`` (D7).

    The snapshot is taken **before** anything is cleared and is what makes the
    Change row self-sufficient for undo *and* what feeds the next generation's
    revision block: the old passage exists nowhere else once this returns.

    Deleting the Quick check is safe by D2, not by a guard here — an unengaged
    lesson has no Attempt, and unengaged is what the re-validation above just
    proved against live state.
    """
    snapshots: list[RevisionSnapshot] = []
    quick_checks = QuickCheckRepository(session)
    lessons = LessonRepository(session)
    for revision in _revisions(operations):
        lesson = live.lesson(revision.lesson_id)
        if lesson is None:  # pragma: no cover - the predicates rejected it first
            continue
        quick_check = await quick_checks.get_for_lesson(lesson.id)
        snapshots.append(
            RevisionSnapshot(
                lesson_id=str(lesson.id),
                title=lesson.title,
                read_passage=lesson.read_passage,
                generated_at=(
                    None
                    if lesson.generated_at is None
                    else lesson.generated_at.isoformat()
                ),
                instruction=revision.instruction,
                quick_check=(
                    None
                    if quick_check is None
                    else QuickCheckSnapshot(
                        stem=quick_check.stem,
                        options=tuple(quick_check.options),
                        correct_index=quick_check.correct_index,
                        explanation=quick_check.explanation,
                    )
                ),
            )
        )
        await quick_checks.delete_for_lesson(lesson.id)
        await lessons.start_revision(
            lesson_id=lesson.id,
            instruction=revision.instruction,
            title=revision.new_title,
        )
    return replace(inverse, revisions=tuple(snapshots))


async def _apply_additions(
    session: AsyncSession,
    path_id: uuid.UUID,
    operations: Sequence[ShapingOperation],
    live: _LivePath,
    inverse: ChangeInverse,
) -> ChangeInverse:
    """Insert each Addition's lessons, shifting the path to make room (D6).

    **Ascending, with a running offset.** Every ``insert_at_position`` in a
    payload is stated against the *same* snapshot of the path, so applying one
    Addition moves the ground under the next: a payload that inserts at 2 and at
    5 wants its second group after the first group's lessons, at 5 + however
    many landed at 2. Walking ascending and carrying the offset is what makes
    the payload mean one thing rather than depending on the order the operations
    happen to be listed in.

    **Which unit new lessons join.** An Addition without a ``new_unit`` joins the
    unit that owns the position it points at (the lesson currently there, or the
    last unit when appending past the end), taking that lesson's slot and pushing
    the rest of the unit down. That is what keeps a unit's lessons contiguous in
    the total order — the property ``services/paths_read.py`` relies on to render
    the outline in the order the learner actually walks it.

    With a ``new_unit``, the unit order is re-derived from the lesson order
    afterwards (:func:`_renumber_units`), which places the new unit correctly for
    every case that has a correct answer — at a unit boundary, or at the end —
    without inventing a rule for a mid-unit insertion, which cannot keep the
    surrounding unit contiguous however it is placed. That last case is rare (a
    learner asking for a *new unit* in the middle of one) and lands as the new
    unit following the one it split; nothing is silently dropped, and the payload
    the learner consented to is what was applied.
    """
    additions = _additions(operations)
    if not additions:
        return inverse

    lessons_repo = LessonRepository(session)
    units_repo = UnitRepository(session)

    positions = {lesson.id: lesson.position_in_path for lesson, _ in live.lessons}
    slots = {lesson.id: lesson.position_in_unit for lesson, _ in live.lessons}
    unit_of = {lesson.id: lesson.unit_id for lesson, _ in live.lessons}

    added_lessons: list[str] = []
    added_units: list[str] = []
    all_shifts: list[PositionShift] = []
    all_slots: list[UnitSlot] = []
    offset = 0

    for addition in sorted(additions, key=lambda op: op.insert_at_position):
        insert_at = addition.insert_at_position + offset
        count = len(addition.lessons)
        anchor = next(
            (
                lesson_id
                for lesson_id, position in positions.items()
                if position == insert_at
            ),
            None,
        )

        shifts = plan_insertion_shifts(
            [(str(lesson_id), position) for lesson_id, position in positions.items()],
            insert_at=insert_at,
            count=count,
        )
        for shift in shifts:
            await lessons_repo.move_to_position(
                lesson_id=uuid.UUID(shift.lesson_id),
                position_in_path=shift.to_position,
            )
            positions[uuid.UUID(shift.lesson_id)] = shift.to_position
        all_shifts.extend(shifts)

        if addition.new_unit is not None:
            unit = await units_repo.create(
                path_id=path_id,
                position=_NEW_UNIT_POSITION + len(added_units),
                title=addition.new_unit.title,
                summary=addition.new_unit.summary,
            )
            added_units.append(str(unit.id))
            target_unit, first_slot = unit.id, 1
        else:
            target_unit, first_slot = _join_existing_unit(
                anchor, live, positions=positions, slots=slots, unit_of=unit_of
            )
            for lesson_id, slot in list(slots.items()):
                if unit_of[lesson_id] == target_unit and slot >= first_slot:
                    await lessons_repo.move_within_unit(
                        lesson_id=lesson_id, position_in_unit=slot + count
                    )
                    all_slots.append(
                        UnitSlot(
                            lesson_id=str(lesson_id),
                            from_position=slot,
                            to_position=slot + count,
                        )
                    )
                    slots[lesson_id] = slot + count

        for index, proposed in enumerate(addition.lessons):
            row = await lessons_repo.create(
                unit_id=target_unit,
                path_id=path_id,
                position_in_path=insert_at + index,
                position_in_unit=first_slot + index,
                title=proposed.title,
            )
            added_lessons.append(str(row.id))
            positions[row.id] = insert_at + index
            slots[row.id] = first_slot + index
            unit_of[row.id] = target_unit
        offset += count

    unit_positions: tuple[tuple[str, int], ...] = ()
    if added_units:
        unit_positions = await _reorder_units_by_lessons(
            session, live, path_id=path_id, positions=positions, unit_of=unit_of
        )

    return replace(
        inverse,
        added_lesson_ids=tuple(added_lessons),
        added_unit_ids=tuple(added_units),
        shifts=tuple(all_shifts),
        slots=tuple(all_slots),
        units=unit_positions,
    )


def _join_existing_unit(
    anchor: uuid.UUID | None,
    live: _LivePath,
    *,
    positions: Mapping[uuid.UUID, int],
    slots: Mapping[uuid.UUID, int],
    unit_of: Mapping[uuid.UUID, uuid.UUID],
) -> tuple[uuid.UUID, int]:
    """The unit an Addition without a ``new_unit`` joins, and at which slot.

    With an ``anchor`` (a lesson sits at the insertion point) the new lessons
    take that lesson's unit and its display slot, pushing it and its followers
    down — the insertion lands *before* the lesson it names, which is what
    "insert at position k" means everywhere else in this phase.

    Without one the Addition is appending past the end of the path, so it joins
    the unit holding the last lesson, after it. A path with lessons always has
    such a unit; a path with none falls back to the last unit by display order,
    and a path with no units at all cannot be shaped (it is not ``ready``).
    """
    if anchor is not None:
        return unit_of[anchor], slots[anchor]
    if positions:
        # The unit holding the lesson with the highest ``position_in_path`` —
        # the path's total order, not the biggest unit. Picking by
        # ``position_in_unit`` instead would append to whichever unit happens to
        # be longest, which is the same bug as appending to the wrong end.
        tail = max(positions, key=lambda lesson_id: positions[lesson_id])
        unit = unit_of[tail]
        return (
            unit,
            max(slot for lesson_id, slot in slots.items() if unit_of[lesson_id] == unit)
            + 1,
        )
    if not live.units:  # pragma: no cover - a ``ready`` path always has units
        raise _conflict_reason(
            ShapingConflictReason.INVALID_PROPOSAL,
            "this path has no units to add lessons to",
        )
    return live.units[-1].id, 1


async def _reorder_units_by_lessons(
    session: AsyncSession,
    live: _LivePath,
    *,
    path_id: uuid.UUID,
    positions: Mapping[uuid.UUID, int],
    unit_of: Mapping[uuid.UUID, uuid.UUID],
) -> tuple[tuple[str, int], ...]:
    """Re-derive unit display order from the lesson order; return the inverse.

    Called only when an Addition created a unit — otherwise no unit moves and
    there is nothing to record. Each unit is ranked by its **first** lesson's
    ``position_in_path``, which is the only ordering that cannot contradict the
    order the learner walks the path in; a unit with no lessons keeps its
    relative place at the end.

    The returned pairs are the *previous* positions of the units that actually
    moved — undo restores exactly those and leaves everything else alone.
    """
    first_position: dict[uuid.UUID, int] = {}
    for lesson_id, position in positions.items():
        unit_id = unit_of[lesson_id]
        first_position[unit_id] = min(first_position.get(unit_id, position), position)
    units = await UnitRepository(session).list_for_path(path_id)
    ordered = sorted(
        units,
        key=lambda unit: (
            0 if unit.id in first_position else 1,
            first_position.get(unit.id, unit.position),
        ),
    )
    desired = {unit.id: index for index, unit in enumerate(ordered, start=1)}
    # Recorded **before** the renumbering: ``session.execute(update(...))``
    # synchronizes matching in-session ORM objects by default, so reading
    # ``unit.position`` afterwards would report the *new* value and the inverse
    # would come out empty — an undo that silently left the unit order rewritten.
    moved = tuple(
        (str(unit.id), unit.position)
        for unit in live.units
        if desired.get(unit.id) != unit.position
    )
    await _renumber_units(session, desired)
    return moved


async def _renumber_units(
    session: AsyncSession, desired: Mapping[uuid.UUID, int]
) -> None:
    """Assign every unit its position, through a scratch range (see the constant).

    Two passes rather than one because ``UNIQUE (path_id, position)`` is checked
    per row: parking everything far away first means the second pass can write
    any permutation without a transient collision.
    """
    units = UnitRepository(session)
    for index, unit_id in enumerate(desired):
        await units.move_to_position(unit_id=unit_id, position=-(index + 1))
    for unit_id, position in desired.items():
        await units.move_to_position(unit_id=unit_id, position=position)


async def _restore_revision(session: AsyncSession, revision: RevisionSnapshot) -> None:
    """Put one revised lesson back exactly as the snapshot recorded it (D8).

    Including its Quick check row, re-created rather than resurrected — the
    original row was deleted at apply, and its id was never anything a learner
    or a client held. An **Attempt** cannot exist against either the old row or
    the new one: undo is only reachable while the lesson is unengaged.
    """
    lesson_id = uuid.UUID(revision.lesson_id)
    await LessonRepository(session).restore_revision(
        lesson_id=lesson_id,
        title=revision.title,
        read_passage=revision.read_passage,
        generated_at=(
            None
            if revision.generated_at is None
            else datetime.fromisoformat(revision.generated_at)
        ),
    )
    quick_checks = QuickCheckRepository(session)
    await quick_checks.delete_for_lesson(lesson_id)
    if revision.quick_check is not None:
        await quick_checks.create(
            lesson_id=lesson_id,
            stem=revision.quick_check.stem,
            options=list(revision.quick_check.options),
            correct_index=revision.quick_check.correct_index,
            explanation=revision.quick_check.explanation,
        )


# --- conflicts (the coded 409s the card renders, §5.8) ---------------------- #


def _not_found(detail: str) -> HTTPException:
    """A ``404`` through the shared envelope (404-never-403, and never-disclose)."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _conflict_reason(reason: ShapingConflictReason, message: str) -> HTTPException:
    """A ``409 conflict`` carrying the reason code beside a human sentence.

    The envelope's ``code`` is ``conflict`` for every ``409`` in this app and
    stays that way; ``details.reason`` is what lets the proposal card render the
    right state and the right affordance (§5.8 makes that a first-class UX, not
    an error corner). ``app.py``'s handler is what promotes ``message`` out of
    the mapping — without it a coded conflict would answer "request failed".
    """
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"reason": reason.value, "message": message},
    )


def _stale(reason: ShapingConflictReason, violation: str) -> HTTPException:
    """A ``409`` for a Proposal that no longer validates against live state.

    ``violation`` is the shared rulebook's own sentence. It is written for the
    *model* (second person, actionable — ``proposal_violation``'s docstring says
    so), which reads oddly to a learner, so it rides in ``details.violation``
    for the card and the logs while the learner-facing message stays plain. One
    rulebook, two audiences.
    """
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "reason": reason.value,
            "message": (
                "your path has changed since this was proposed, so it no longer "
                "fits. Ask again and the tutor will propose it afresh."
            ),
            "violation": violation,
        },
    )


def _proposal_already_resolved(status_: PathChangeStatus) -> HTTPException:
    """A ``409`` for a Proposal that already produced a Change.

    Both states are terminal for *this* card: an applied Proposal is the path,
    and an undone one has been deliberately taken back — re-applying it would
    resurrect an edit the learner reversed, from a payload validated against a
    path two changes ago. Asking again is the way to redo it, and the shaper has
    the Change history in front of it when they do.
    """
    if status_ is PathChangeStatus.APPLIED:
        return _conflict_reason(
            ShapingConflictReason.ALREADY_APPLIED,
            "this proposal has already been applied to your path",
        )
    return _conflict_reason(
        ShapingConflictReason.ALREADY_UNDONE,
        "this proposal was applied and then undone; ask again to redo it",
    )
