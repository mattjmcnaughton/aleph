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
on that engine rather than reached for across a module boundary. This is the one
piece of this module that is known-wrong-on-purpose; nothing else here is waiting
on a follow-up.

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
from dataclasses import dataclass
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

from aleph.agents.shaper import PROPOSE_PATH_EDIT_TOOL_NAME, build_shaper_agent
from aleph.config import settings as global_settings
from aleph.db import new_session
from aleph.dtos.shaping import ProposalPayloadDTO
from aleph.dtos.tutor import (
    MessageDeltaDTO,
    MessageDoneDTO,
    TutorErrorCode,
)
from aleph.models import ConversationKind, PathStatus
from aleph.repositories import ConversationRepository
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
from aleph.services.tutor_context import assemble_shaping_context

if TYPE_CHECKING:
    import uuid
    from collections.abc import AsyncIterator, Callable
    from contextlib import AbstractAsyncContextManager

    from pydantic_ai.models import Model
    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.agents.shaper import ShaperDeps
    from aleph.config import Settings
    from aleph.models import MessageSource, Path
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
