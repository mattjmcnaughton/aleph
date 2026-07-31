"""The turn service: one learner question in, one streamed reply out (AL-220).

This is Phase 2 TDD §5.5 in code — the whole lifecycle of a turn, and the only
place the SSE transport (§5.4) is spoken:

1. **Admit** (:meth:`TutorTurnService.admit`, before any byte is streamed):
   the lesson is on the path and has generated content, the conversation has no
   reply in flight, the learner is under the daily cap, and the context assembles.
   Every failure here is an ordinary JSON error envelope — ``404``/``409``/``429``
   — because SSE starts only once the turn is admitted.
2. **Stream** (:meth:`TutorTurnService.stream`): under the tutor semaphore and a
   whole-stream ``asyncio.timeout``, the agent's event iterator is translated to
   ``delta`` / ``tutor_check`` frames as they happen.
3. **Settle**: on clean completion one transaction writes the conversation (lazily)
   and the turn pair, then ``done``. On any failure, timeout or disconnect,
   **nothing is persisted** and an ``error`` frame goes out if the socket is
   still open (D2).

**Why a producer task and a queue.** The heartbeat (``: ping`` every
``SSE_HEARTBEAT_SECONDS`` of model silence) and the whole-stream timeout are two
clocks that have to run while the response generator is suspended at a ``yield``.
Wrapping the generator body in ``asyncio.timeout`` would arm a cancellation that
could fire *while Starlette is sending* — cancelling code that is not ours. So
the reply runs as its own task, pushing pre-encoded frames into a queue, and the
generator does nothing but drain the queue with a per-item timeout, emitting a
heartbeat whenever the drain comes up empty. The producer owns the model, the
transaction and the error mapping; the consumer owns the socket.

**Nothing is persisted on disconnect.** When the client goes away Starlette
closes the generator, whose ``finally`` cancels the producer — usually while it
is awaiting the model, so the transaction never begins. Two races round that off,
both the same accepted category (D2 is whole-turn-or-nothing, and both outcomes
*are* a whole turn or nothing):

* a disconnect landing in the microseconds between ``COMMIT`` and the ``done``
  frame leaves a persisted turn the client never saw acknowledged; it appears
  whole on the next thread read;
* a cancellation landing *mid-commit* is decided by the database, not by us —
  the transaction commits atomically or rolls back atomically, so the turn is
  still whole or absent, never half.

Closing either would cost a two-phase handshake this feature does not need.

**The request's session is released before the stream starts.** Admission runs
on its own short-lived session (the :func:`aleph.db.new_session` seam) and the
settle transaction opens another; the *request's* session — the one
``OwnedPath`` pinned a pooled connection with — is closed by the route before it
returns the response (see ``routers/v1/tutor.py``). So a reply that spends 90s
waiting on a provider holds no pooled database connection, which, at
``MAX_CONCURRENT_TUTOR_REPLIES`` plus everything queued behind it, is how a
streaming endpoint takes the rest of the API down with it.

**The conversation's reservation is released by the response object**, not by
the generator below: a generator that is never started never runs its
``finally``. See :meth:`TutorTurnService.stream` and ``ReservedStream`` in the
router.

Layering: ``routers -> services -> (agents, repositories)``. This module binds
the model (agents never do), reads config, and owns the unit of work; the router
above it does auth, ownership, the picker gate and the response object.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import HTTPException, status
from pydantic_ai.exceptions import ModelAPIError, UnexpectedModelBehavior
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolReturnPart,
)
from pydantic_ai.run import AgentRunResultEvent
from sqlalchemy.exc import IntegrityError

from aleph import events
from aleph.agents.tutor import TUTOR_CHECK_TOOL_NAME, build_tutor_agent
from aleph.config import settings as global_settings
from aleph.db import new_session
from aleph.dtos.tutor import (
    MessageDeltaDTO,
    MessageDoneDTO,
    MessageErrorDTO,
    TutorCheckDTO,
    TutorErrorCode,
)
from aleph.models import ConversationKind
from aleph.repositories import ConversationRepository, LessonRepository
from aleph.services.generation import usage_tokens
from aleph.services.lifecycle import (
    ConversationBusyError,
    ReplyReservation,
    TutorReplyLimiter,
)
from aleph.services.openrouter import resolve_model
from aleph.services.rate_limit import build_daily_rate_limiter
from aleph.services.sse import sse_event, sse_heartbeat
from aleph.services.tutor_context import (
    LessonContextUnavailableError,
    assemble_lesson_context,
)

if TYPE_CHECKING:
    import uuid
    from collections.abc import AsyncIterator, Callable
    from contextlib import AbstractAsyncContextManager

    from pydantic_ai.models import Model
    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.config import Settings
    from aleph.models import MessageSource, Path
    from aleph.services.tutor_context import AssembledContext

    SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
    ResolveModel = Callable[[str], Model]

logger = structlog.get_logger(__name__)


# --- learner-facing failure copy ------------------------------------------------
#
# PRD §5.7's named wording gap: an upstream failure must not be reported as the
# learner's connection problem, and a failed reply must say plainly that nothing
# was kept — the question is still in their composer, and re-sending is the whole
# remedy. The machine-readable ``code`` rides alongside so the rail can word
# things differently again if it wants to.
_FAILURE_COPY: dict[TutorErrorCode, str] = {
    TutorErrorCode.TIMEOUT: (
        "The tutor took too long to finish that answer, so I stopped waiting. "
        "Nothing was saved — ask again when you're ready."
    ),
    TutorErrorCode.UPSTREAM_ERROR: (
        "The tutor couldn't finish that answer. Nothing was saved — ask again "
        "when you're ready."
    ),
    TutorErrorCode.INTERNAL_ERROR: (
        "Something went wrong on our side while answering. Nothing was saved — "
        "ask again when you're ready."
    ),
}


class TutorReplyError(RuntimeError):
    """A failed reply, carrying the ``code`` its ``error`` frame will report.

    Internal to this module: the router never sees one, because by the time a
    reply can fail the response is already a ``200`` text/event-stream.
    """

    def __init__(self, code: TutorErrorCode) -> None:
        super().__init__(code.value)
        self.code = code

    @property
    def frame(self) -> str:
        return sse_event(
            "error", MessageErrorDTO(code=self.code, message=_FAILURE_COPY[self.code])
        )


class _EventTranslationError(RuntimeError):
    """A failure in *this module's* event translation, not the model's turn.

    Deliberately **not** a :class:`TutorReplyError`: it carries no learner-facing
    code, so it falls past :meth:`TutorTurnService._produce`'s ``TutorReplyError``
    clause into the ``Exception`` clause, which logs a traceback (the chained
    ``__cause__`` is the real bug) and reports ``internal_error``. Filing a
    DTO/schema drift as ``upstream_error`` would leave it invisible in the logs
    and blame the provider.
    """


@dataclass(frozen=True)
class AdmittedTurn:
    """A turn that passed every pre-stream gate and holds its conversation.

    Everything :meth:`TutorTurnService.stream` needs, and nothing bound to a
    database session: ``context`` is plain dataclasses and pydantic-ai messages,
    so the stream holds no connection while it waits on the model.

    Holding the conversation's ``reservation`` is part of what this object *is*,
    which is why admission and streaming are two calls rather than one. The
    reservation is **not** released by the stream: see
    :meth:`TutorTurnService.stream` and :meth:`TutorTurnService.release`.

    ``account_id`` and ``position_in_path`` are here only so the whole lifecycle
    can stamp its product events (TDD §9) without re-reading the path or the
    lesson from a stream that deliberately holds no session.
    """

    account_id: uuid.UUID
    path_id: uuid.UUID
    lesson_id: uuid.UUID
    position_in_path: int
    content: str
    source: MessageSource
    model_id: str
    context: AssembledContext
    reservation: ReplyReservation


@dataclass
class _ReplyResult:
    """What one completed agent run produced, before it is persisted."""

    text: str
    tutor_check: dict[str, Any] | None


@dataclass
class _ReplyMeasurement:
    """What ``tutor_reply_completed`` reports, filled in *as the reply runs*.

    A mutable out-parameter rather than a return value because the event fires
    on **every** resolution (TDD §9) and the failing resolutions never return
    anything: a reply that streamed for two seconds and then died still has a
    time to first token, and reporting it as null would make a slow failure
    indistinguishable from an instant one on the guardrail panel.
    """

    ttft_ms: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class TutorTurnService:
    """Runs the §5.5 lifecycle for one turn at a time, per request.

    Seams are constructor-injected in the Phase 1 style so tests drive the whole
    endpoint deterministically: ``_resolve_model`` (patched to a ``FunctionModel``
    for streamed stubs and failure injection), ``_session_factory`` (the settle
    transaction), ``_replies`` (the D9 bounds) and ``_config``.

    The task registry is deliberately **not** used (D9): a reply is
    request-scoped, so there is no background task to keep alive, nothing to
    reclaim after a crash, and no state machine to recover.
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
            max_concurrent=config.max_concurrent_tutor_replies
        )

    # -- 1. admission (everything that can still be an ordinary error) ------- #

    async def admit(
        self,
        *,
        path: Path,
        is_admin: bool,
        lesson_id: uuid.UUID,
        content: str,
        source: MessageSource,
        model_id: str,
    ) -> AdmittedTurn:
        """Run §5.5 steps 1-4, or raise the ``HTTPException`` the route returns.

        ``path`` is the owned row the router already resolved; the reads here
        open their **own** short-lived session so the request's session is free
        for the duration of the stream (see the module docstring).

        Order is load-bearing: the lesson is validated before the conversation
        is claimed (a bad lesson id must not lock the thread), the claim comes
        before the cap check (D9 before D8, so a burst is refused as a conflict
        rather than eating quota), and the reservation is released on *any*
        failure after it — otherwise a rejected send would wedge the
        conversation until the process restarted.

        On success the caller owns the returned turn's ``reservation`` and
        **must** hand it back to :meth:`release`; the route does that from the
        response object's ``finally`` (see :meth:`stream`).
        """
        async with self._session_factory() as session:
            await self._require_generated_lesson(
                session, path_id=path.id, lesson_id=lesson_id
            )
            try:
                reservation = self._replies.reserve(path.id)
            except ConversationBusyError as exc:
                raise _conflict(
                    "a reply is already in flight on this conversation"
                ) from exc
            try:
                limiter = build_daily_rate_limiter(session)
                await limiter.check_tutor_message(
                    user_id=path.user_id, is_admin=is_admin
                )
                context = await assemble_lesson_context(
                    session, path=path, lesson_id=lesson_id
                )
            except LessonContextUnavailableError as exc:
                # The router already excluded both states, so this is a raced
                # delete. It is the D2 failed path either way — nothing is
                # persisted — and pre-stream that means an ordinary envelope.
                self._replies.release(reservation)
                raise _conflict("that lesson has no content to answer from") from exc
            except BaseException:
                self._replies.release(reservation)
                raise

        # Admission — not persistence — is when the learner's question exists:
        # a reply that later fails is our failure, not an un-asked question, and
        # D2 would otherwise erase it from the adoption and primary metrics
        # entirely. A send refused above emits nothing; it never became a turn.
        events.emit_tutor_message_sent(
            account_id=path.user_id,
            path_id=path.id,
            lesson_id=lesson_id,
            position_in_path=context.deps.position_in_path,
            source=source.value,
        )
        return AdmittedTurn(
            account_id=path.user_id,
            path_id=path.id,
            lesson_id=lesson_id,
            position_in_path=context.deps.position_in_path,
            content=content,
            source=source,
            model_id=model_id,
            context=context,
            reservation=reservation,
        )

    def release(self, turn: AdmittedTurn) -> None:
        """Free the conversation ``turn`` claimed at admission. Idempotent.

        Called from the one frame that is guaranteed to run once a response
        object exists — ``ReservedStream.__call__``'s ``finally`` in the router
        — rather than from the response generator, which may never be started
        at all (see :meth:`stream`).
        """
        self._replies.release(turn.reservation)

    async def _require_generated_lesson(
        self, session: AsyncSession, *, path_id: uuid.UUID, lesson_id: uuid.UUID
    ) -> None:
        """``404`` unless the lesson is this path's; ``409`` until it is generated.

        ``409`` (rather than ``404`` or an empty reply) is Phase 1's "not
        generated yet" semantics: the lesson exists and is the caller's, but
        lesson scope is empty until a Read passage exists, so there is nothing
        for the tutor to ground on and the request conflicts with the lesson's
        state.
        """
        lesson = await LessonRepository(session).get(lesson_id)
        if lesson is None or lesson.path_id != path_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="lesson not found"
            )
        if lesson.read_passage is None:
            raise _conflict("that lesson has not been generated yet")

    # -- 2/3. the stream (the only place SSE is spoken) --------------------- #

    async def stream(self, turn: AdmittedTurn) -> AsyncIterator[str]:
        """Yield the turn's SSE frames until a terminal ``done`` or ``error``.

        Drains the producer's queue, filling every ``SSE_HEARTBEAT_SECONDS`` of
        silence with a ``: ping`` comment so no proxy idle-timeout kills a stream
        that is merely waiting on a slow first token (§5.4).

        The ``finally`` cancels the producer: a disconnect must not leave a
        model call — or a transaction — running for a learner who left. It is
        safe to run twice and cannot raise.

        **It deliberately does not release the reservation.** An async generator
        that is never started never runs its ``finally`` — not even on an
        explicit ``aclose`` (PEP 525) — and Starlette can create this response
        and then cancel it before the first ``__anext__`` when the client
        disconnects between admission and the first byte. A cleanup that lives
        here would leak the claim permanently and 409 that conversation until
        the process restarted. The release therefore belongs to the response
        object wrapping this generator (``ReservedStream`` in the router), whose
        ``__call__`` always runs, and it releases the *token* rather than the
        path id so a late release can never free a successor's claim.
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
        self, turn: AdmittedTurn, queue: asyncio.Queue[str | None]
    ) -> None:
        """Run the reply and push its frames; ``None`` closes the stream.

        Every exit path pushes exactly one terminal frame (``done`` or ``error``)
        and then the sentinel, so the consumer never has to reason about how the
        reply ended — and every exit path emits exactly one
        ``tutor_reply_completed`` (TDD §9), which is why the emission lives in
        the ``finally`` and the outcome is decided by which clause was taken.

        **The three outcomes.** ``success`` is the settled turn (a refusal
        included — an over-the-boundary ask answered gracefully is a real turn,
        not machine-tagged this phase, D5). ``failure`` is any error, upstream or
        ours. ``stopped`` is a :class:`asyncio.CancelledError`, which is what a
        learner ending their own turn looks like from here: the consumer's
        ``finally`` cancels this task when Starlette closes the generator, and
        the rail's stop affordance and a plain disconnect are the same event on
        the socket. It is re-raised untouched — cancellation is not ours to
        swallow — but it is *not* a failure, and filing it as one would put
        learner behaviour into the reply-failure guardrail.
        """
        started = time.perf_counter()
        outcome = "failure"
        measured = _ReplyMeasurement()
        try:
            async with self._replies.slot():
                # The permit bounds the model run only; the timeout is inside it
                # so queue time is not charged against the reply's budget — the
                # same shape as the generation permit and its per-call timeout.
                reply = await self._run_reply(turn, queue, measured)
            learner_id, tutor_id = await self._settle(turn, reply)
            await queue.put(
                sse_event(
                    "done",
                    MessageDoneDTO(
                        learner_message_id=learner_id, tutor_message_id=tutor_id
                    ),
                )
            )
            outcome = "success"
        except TutorReplyError as exc:
            await queue.put(exc.frame)
        except asyncio.CancelledError:
            outcome = "stopped"
            raise
        except Exception:
            logger.exception("tutor_reply_unhandled_error", path_id=str(turn.path_id))
            await queue.put(TutorReplyError(TutorErrorCode.INTERNAL_ERROR).frame)
        finally:
            # PRD §5.9's "latency to first token" lives here and nowhere else —
            # no Phase 1 event has it, because no Phase 1 surface streams.
            events.emit_tutor_reply_completed(
                account_id=turn.account_id,
                path_id=turn.path_id,
                lesson_id=turn.lesson_id,
                position_in_path=turn.position_in_path,
                outcome=outcome,
                ttft_ms=measured.ttft_ms,
                duration_ms=round((time.perf_counter() - started) * 1000),
                prompt_tokens=measured.prompt_tokens,
                completion_tokens=measured.completion_tokens,
                total_tokens=measured.total_tokens,
            )
            await queue.put(None)

    async def _run_reply(
        self,
        turn: AdmittedTurn,
        queue: asyncio.Queue[str | None],
        measured: _ReplyMeasurement,
    ) -> _ReplyResult:
        """Stream one agent run, translating its events to SSE frames.

        Bounded by ``TUTOR_REPLY_TIMEOUT`` so a hung provider ends in ``error``
        and never in a dead stream. Everything that goes wrong *on the model's
        side* is the model's turn failing, so it is reported as
        ``upstream_error`` — including a run that exhausts the agent's shared
        ``retries`` budget (which surfaces as ``UnexpectedModelBehavior``).

        The ``upstream_error`` catch-all is scoped to exactly that: resolving
        the model and pulling the next event off the run. Translating an event
        — validating a posed check against :class:`TutorCheckDTO`, encoding a
        frame — is *this module's* code, so a failure there is a bug of ours and
        is wrapped in :class:`_EventTranslationError` to fall through to
        :meth:`_produce`, which logs it with a traceback and reports
        ``internal_error``. A schema drift between the agent's tool and the wire
        DTO must not be filed as "the provider is down".
        """
        agent = build_tutor_agent()
        text: str | None = None
        check: dict[str, Any] | None = None
        # Tool calls observed but not yet validated, by call id. A check is only
        # delivered once its arguments have passed the agent's validator — a
        # ``ModelRetry``'d call posed nothing.
        posed: dict[str, dict[str, Any]] = {}
        started = time.perf_counter()

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
                            if measured.ttft_ms is None:
                                measured.ttft_ms = round(
                                    (time.perf_counter() - started) * 1000
                                )
                            await queue.put(
                                sse_event("delta", MessageDeltaDTO(text=delta))
                            )
                        elif isinstance(event, FunctionToolCallEvent):
                            _record_call(posed, event)
                        elif isinstance(event, FunctionToolResultEvent):
                            payload = _accepted_check(posed, event)
                            if payload is not None and check is None:
                                # One shape for the wire and the row: the DTO is
                                # what the rail renders on the event *and* what
                                # the thread read returns later, so the
                                # check-answer endpoint has an ``answered_index``
                                # key to overwrite rather than one to invent.
                                card = TutorCheckDTO.model_validate(payload)
                                check = card.model_dump(mode="json")
                                await queue.put(sse_event("tutor_check", card))
                                # Emitted where the card reaches the rail, which
                                # is what "shown" means (TDD §9). A call the
                                # validator rejected never gets here.
                                events.emit_tutor_check_shown(
                                    account_id=turn.account_id,
                                    path_id=turn.path_id,
                                    lesson_id=turn.lesson_id,
                                    position_in_path=turn.position_in_path,
                                )
                        elif isinstance(event, AgentRunResultEvent):
                            text = str(event.result.output)
                            (
                                measured.prompt_tokens,
                                measured.completion_tokens,
                                measured.total_tokens,
                            ) = usage_tokens(event.result)
                    except Exception as exc:
                        # Ours, not the model's — see the method docstring.
                        raise _EventTranslationError from exc
        except TimeoutError as exc:
            raise TutorReplyError(TutorErrorCode.TIMEOUT) from exc
        except (UnexpectedModelBehavior, ModelAPIError) as exc:
            logger.warning("tutor_reply_model_failed", error=repr(exc))
            raise TutorReplyError(TutorErrorCode.UPSTREAM_ERROR) from exc
        except _EventTranslationError:
            raise
        except Exception as exc:
            logger.warning("tutor_reply_stream_failed", error=repr(exc))
            raise TutorReplyError(TutorErrorCode.UPSTREAM_ERROR) from exc

        if text is None:  # pragma: no cover - a completed run always has output
            raise TutorReplyError(TutorErrorCode.UPSTREAM_ERROR)
        return _ReplyResult(text=text, tutor_check=check)

    async def _settle(
        self, turn: AdmittedTurn, reply: _ReplyResult
    ) -> tuple[uuid.UUID, uuid.UUID]:
        """One transaction: the conversation (if new) and the whole turn (D2).

        The ids are read *before* the commit — ``insert_turn`` flushes, so they
        exist — because the session closes on the way out of this block.

        An ``IntegrityError`` here means the position pair collided, i.e. the
        per-conversation reservation was somehow bypassed. That is a failed
        reply, not a 500 with half a turn on the wire: the transaction rolls
        back with the session, and the learner is told the reply did not land.
        """
        try:
            async with self._session_factory() as session:
                repository = ConversationRepository(session)
                conversation, created = await repository.upsert_for_path(
                    turn.path_id, kind=ConversationKind.LESSON
                )
                learner, tutor = await repository.insert_turn(
                    conversation_id=conversation.id,
                    lesson_id=turn.lesson_id,
                    learner_content=turn.content,
                    source=turn.source,
                    tutor_content=reply.text,
                    tutor_check=reply.tutor_check,
                )
                ids = (learner.id, tutor.id)
                await session.commit()
            # After the commit, so the event never claims a conversation a
            # rolled-back transaction did not leave behind. ``created`` is the
            # lazy upsert's own answer, which is what makes this fire exactly
            # once per path rather than once per first-turn-shaped request.
            if created:
                events.emit_tutor_conversation_started(
                    account_id=turn.account_id,
                    path_id=turn.path_id,
                    lesson_id=turn.lesson_id,
                    position_in_path=turn.position_in_path,
                )
            return ids
        except IntegrityError as exc:
            logger.warning("tutor_turn_insert_conflicted", error=repr(exc))
            raise TutorReplyError(TutorErrorCode.INTERNAL_ERROR) from exc


def _text_of(event: object) -> str:
    """The text a stream event carries, or ``""`` if it carries none.

    Both shapes count: the first fragment of a text part arrives as a
    ``PartStartEvent`` (already carrying content), later ones as
    ``PartDeltaEvent``. Missing the first would silently drop the opening words
    of every reply.
    """
    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
        return event.part.content
    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
        return event.delta.content_delta
    return ""


def _record_call(
    posed: dict[str, dict[str, Any]], event: FunctionToolCallEvent
) -> None:
    """Remember a ``pose_tutor_check`` call's arguments until it is validated."""
    part = event.part
    if part.tool_name != TUTOR_CHECK_TOOL_NAME or part.tool_call_id is None:
        return
    try:
        arguments = part.args_as_dict()
    except (ValueError, TypeError, json.JSONDecodeError):
        # Malformed JSON arguments: pydantic-ai will feed the model a retry, and
        # there is nothing here worth delivering to the learner.
        return
    posed[part.tool_call_id] = arguments


def _accepted_check(
    posed: dict[str, dict[str, Any]], event: FunctionToolResultEvent
) -> dict[str, Any] | None:
    """The check payload a *successful* tool return corresponds to, if any.

    A ``RetryPromptPart`` result means the arguments were rejected
    (``validate_tutor_check`` raised ``ModelRetry``) — the model posed nothing,
    so nothing is delivered and nothing is persisted. Observing the *result*
    rather than the call is what makes that distinction, and it is still
    mid-stream: the frame may land before, between, or after the reply's own
    deltas — the client attaches it to the message, not to a position.
    """
    part = event.part
    if not isinstance(part, ToolReturnPart) or part.tool_name != TUTOR_CHECK_TOOL_NAME:
        return None
    return posed.pop(part.tool_call_id, None)


def _conflict(detail: str) -> HTTPException:
    """A ``409`` the shared envelope renders with code ``conflict``."""
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


# The module-level singleton the router uses, mirroring
# ``generation.generation_orchestrator``: one object per process, so the
# in-flight reservations and the semaphore are genuinely process-wide, and tests
# patch its private seams in place.
tutor_turn_service = TutorTurnService()
