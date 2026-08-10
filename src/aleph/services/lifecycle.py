"""Generation lifecycle: task registry, concurrency bound, reconciler, shutdown.

This is AL-041 — the runtime scaffolding around AL-040's
:class:`~aleph.services.generation.GenerationOrchestrator`. The orchestrator owns
the generation *logic* (claiming, the serial per-path chain, failure mapping) and
exposes two runtime seams (``spawn``, ``model_slot``); this module supplies them
and drives the process:

* :class:`TaskRegistry` — the ``spawn`` wrapper. A bare ``asyncio.create_task``
  whose reference is dropped can be garbage-collected mid-flight (swallowing its
  exceptions); the registry holds a **strong ref** to every spawned task, drops
  it on completion via a done-callback, and is what shutdown cancels (§5.4
  invariant).
* A process-wide :class:`asyncio.Semaphore` — the ``model_slot``, bound around
  each generation so aggregate spend/latency spikes queue instead of fanning out
  (``MAX_CONCURRENT_GENERATIONS``, §5.4). The permit is acquired *before* the
  claim (it spans one claim + context load + model call), so a row never commits
  ``generating`` and then queues unbounded on the semaphore — which under a spike
  would let a healthy queued row go stale mid-queue and be double-claimed. It
  bounds one generation, never the whole task, so the serial per-path chain
  (which acquires/releases one permit per lesson) does not deadlock against
  itself.
* :class:`Reconciler` — the in-process loop (started in the FastAPI lifespan) that
  every ``RECONCILER_INTERVAL`` scans for claimable work (stale ``generating``
  rows, unfilled prefetch windows) and drives the same idempotent
  ``resume_path`` through the registry. Poll-as-trigger stays a redundant second
  driver, but a crashed chain now resumes within one tick instead of waiting for
  a learner's poll, and work with no active poller drains on its own.
* :class:`GenerationLifecycle` — the single object the app lifespan starts and
  stops. Start binds the orchestrator's seams and launches the reconciler; stop
  halts the reconciler, cancels in-flight generation tasks, and unbinds. Rows
  left mid-flight revert via stale recovery — the state machine makes
  cancellation safe, so there is no cleanup-on-cancel logic anywhere (§5.4).

**Phase 2 adds a second, deliberately separate bound** (Phase 2 TDD D9):
:class:`TutorReplyLimiter`. A tutor reply is request-scoped — a learner is
present, waiting mid-sentence — so it gets its *own* semaphore
(``MAX_CONCURRENT_TUTOR_REPLIES``) rather than sharing generation's, and it
does **not** use the task registry: there is no background work to keep alive
and nothing to reclaim if the process dies. The same object owns the
per-conversation in-flight guard, because the two are one policy — "how much
tutoring may run at once, and never twice on one thread".

**Phase 6 adds a third, its own bound again** (Phase 6 TDD D14, ticket
AL-521): a second ``asyncio.Semaphore`` sized by
``MAX_CONCURRENT_BRIEF_RESEARCH``, bound to the module-level
``services.briefing.briefing_service`` singleton alongside the existing one.
Research is the most expensive generation in the product per unit of output,
and it must never be able to starve lesson generation, so it gets its own
pool rather than sharing ``max_concurrent_generations`` — the same argument
that split the tutor's semaphore from generation's, one workload over. It
**reuses the SAME** :class:`TaskRegistry` generation binds (TDD §2: "the
registry ... reused as-is") — background research is exactly the same kind
of work (strong ref, shutdown-cancelled, self-healing via stale recovery) as
outline/lesson generation, so it needs no second registry, only a second
semaphore. **This was the only change AL-521 made to this module.**

**A later ticket adds the retriever itself** (the AL-521/AL-523 handoff gap:
AL-521 shipped ``briefing_service`` bound to ``_UnconfiguredRetriever`` on
purpose and recorded production wiring as belonging elsewhere; AL-523 shipped
``ExaRetriever`` under a brief that said "nothing above the seam changes" and
recorded the wiring as belonging elsewhere too — so nothing ever bound one).
:meth:`GenerationLifecycle.start` now passes a live ``ExaRetriever`` to
``briefing_service.bind_runtime`` when ``EXA_API_KEY`` is configured, and
``None`` (no rebind — the loud ``_UnconfiguredRetriever`` default stays
bound, TDD §5.7) when it is not, so startup still succeeds without the key
(TDD §12) and a Beat's research fails visibly rather than silently either way.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from aleph.config import settings as global_settings

# The one default session factory: a fresh short-lived session from the
# module-level maker, resolved at call time (the AL-010 landmine the
# orchestrator guards against). It lives in ``db.py`` so every consumer shares
# one public seam rather than borrowing a private name from a sibling service.
from aleph.db import new_session
from aleph.repositories import PathRepository
from aleph.services.briefing import briefing_service
from aleph.services.retrieval import ExaRetriever

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable, Coroutine
    from contextlib import AbstractAsyncContextManager
    from typing import Any

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.config import Settings
    from aleph.services.generation import GenerationOrchestrator
    from aleph.services.retrieval import Retriever

    SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

logger = structlog.get_logger(__name__)


class ConversationBusyError(RuntimeError):
    """A reply is already in flight on this conversation (Phase 2 D9).

    Raised by :meth:`TutorReplyLimiter.reserve`. ``services/tutor.py`` maps it to
    the ``409 conflict`` the send endpoint answers *before* any stream opens —
    the composer is disabled client-side, but the server is the enforcer.
    """


@dataclass(eq=False)
class ReplyReservation:
    """One claim on one conversation — the receipt :meth:`reserve` hands back.

    **Why a token and not the ``path_id``.** Release has to be idempotent (the
    frame that owns it may run after a failure, a timeout, or a disconnect), and
    an idempotent *keyed* release is unsafe: between one reply's release and a
    late second release for the same key, a new request can legitimately reserve
    the same conversation, and the late call would free the successor's claim —
    re-opening the D9 race it exists to close. A token releases only the claim
    it *is*: :meth:`TutorReplyLimiter.release` drops it only while it is still
    the conversation's current holder, so a late or duplicated release is a
    genuine no-op rather than a silent theft.

    Identity, not value, is what distinguishes two successive claims on one path
    (``eq=False``): two reservations for the same ``path_id`` are different
    objects and the limiter compares them with ``is``.
    """

    path_id: uuid.UUID


class TutorReplyLimiter:
    """The tutor's two runtime bounds: a semaphore + per-conversation exclusion.

    **The semaphore** (``MAX_CONCURRENT_TUTOR_REPLIES``) is process-wide and
    *its own*, never generation's (D9): batch prefetch work must never make a
    learner wait mid-sentence, and the two workloads have opposite latency
    profiles. It bounds the model run only — not the whole request — so queue
    time is not charged against ``TUTOR_REPLY_TIMEOUT``, exactly as the
    generation permit sits outside its per-call timeout. Phase 2B's shaping
    replies **share** that one semaphore through the ``semaphore`` constructor
    argument (2B D11) while keeping their own reservations — same workload
    class, same pool; different conversations, different locks.

    **The reservation** is one in-flight reply per conversation, keyed by
    ``path_id`` (a conversation is one-per-path and created lazily, so the path
    is the stable identity — there may be no conversation row yet). It is what
    makes D2's position assignment race-free in practice: two concurrent sends
    can never compute the same ``max(position)``, so
    ``uq_messages_conversation_position`` stays a loud backstop rather than a
    live failure mode.

    :meth:`reserve` and :meth:`release` are separate calls rather than a context
    manager because the acquisition and the release genuinely happen in
    different frames: the route reserves *before* it returns a response (so the
    conflict is an ordinary JSON ``409``, pre-stream), and the **response
    object** releases in a ``finally`` around its own ``__call__`` (so a
    failure, a timeout, a disconnect — and, critically, a response whose body
    generator is never started at all — all free the conversation).

    ``reserve`` hands back a :class:`ReplyReservation` and ``release`` takes it,
    which is what makes the release idempotent *per claim* rather than per key;
    see :class:`ReplyReservation` for why the difference matters.

    **Scope: one process.** The reservation is in-memory, so it does not
    coordinate across machines — the app runs as a single Fly machine today, and
    the database's unique constraint is the honest backstop if that ever stops
    being true. A distributed lock would be real machinery for a risk this
    deployment does not have.
    """

    def __init__(
        self,
        *,
        max_concurrent: int | None = None,
        semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        """Bound this limiter by its own semaphore, or by one it **shares**.

        ``max_concurrent`` sizes a fresh semaphore — the Phase 2A arrangement,
        unchanged. ``semaphore`` adopts an existing one instead, which is Phase
        2B D11: shaping replies and in-lesson replies are the same workload
        class (a learner waiting mid-sentence), so they queue against *one*
        bound rather than two pools that can starve each other. The
        per-conversation reservations stay this limiter's own either way — that
        is the whole point of a second object, since a shaping reply must not
        make the in-lesson thread read as busy (W21).

        **Exactly one** of the two, and both ends of that are enforced. Neither
        is a silent unbounded fan-out at the one seam that exists to prevent it.
        Both is worse than neither: one of the two would have to lose silently,
        and a caller who passed ``max_concurrent=8`` alongside a shared semaphore
        would believe it had a bound of 8 while queueing against somebody else's
        pool — a misconfiguration that never raises and only ever shows up as a
        load figure nobody can explain.
        """
        if max_concurrent is not None and semaphore is not None:
            raise ValueError(
                "a reply limiter takes max_concurrent (its own bound) or an "
                "existing semaphore to share (D11) — not both."
            )
        if semaphore is None:
            if max_concurrent is None:
                raise ValueError(
                    "a reply limiter needs either max_concurrent (its own bound) "
                    "or an existing semaphore to share (D11)."
                )
            # Constructing a Semaphore needs no running loop (3.10+ binds lazily
            # on first use), so this is safe at app-assembly time.
            semaphore = asyncio.Semaphore(max_concurrent)
        self._semaphore = semaphore
        self._in_flight: dict[uuid.UUID, ReplyReservation] = {}

    def slot(self) -> asyncio.Semaphore:
        """The permit to hold around one model run (an async context manager)."""
        return self._semaphore

    @property
    def in_flight(self) -> frozenset[uuid.UUID]:
        """The conversations (by path id) with a reply in flight right now."""
        return frozenset(self._in_flight)

    def reserve(self, path_id: uuid.UUID) -> ReplyReservation:
        """Claim the conversation, or raise :class:`ConversationBusyError`.

        Atomic by construction: the check and the insert happen with no
        ``await`` between them, so on a single event loop no two coroutines can
        both see the conversation free.

        Returns the claim's :class:`ReplyReservation` — the only thing
        :meth:`release` accepts.
        """
        if path_id in self._in_flight:
            raise ConversationBusyError(
                f"a tutor reply is already in flight for path {path_id}"
            )
        reservation = ReplyReservation(path_id=path_id)
        self._in_flight[path_id] = reservation
        return reservation

    def release(self, reservation: ReplyReservation) -> None:
        """Free the conversation this reservation claimed. Idempotent per claim.

        Releasing the *same* reservation twice is a no-op; releasing a stale one
        after the conversation has been re-reserved is a no-op too, which is the
        whole point of the token (see :class:`ReplyReservation`). Deliberately
        forgiving and unable to raise: the caller is a ``finally``, and a double
        release must never turn a failed reply into a 500.
        """
        if self._in_flight.get(reservation.path_id) is reservation:
            del self._in_flight[reservation.path_id]


class TaskRegistry:
    """Holds strong references to spawned generation tasks (§5.4 invariant).

    Used as the orchestrator's ``spawn`` seam. Every spawned coroutine becomes a
    tracked :class:`asyncio.Task`; a done-callback discards it once it finishes,
    so the set is exactly the *live* tasks. Shutdown cancels them all and awaits
    the drain — the state machine makes that safe (stale recovery), so no
    task carries cleanup-on-cancel logic.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()
        # Flipped by ``cancel_all`` (shutdown). A spawn after the registry is
        # closed lands in a set nothing will drain again — an orphan that can
        # outlive the process. We do not have the machinery to reject it here
        # (the seam signature must keep spawning), so we make it *visible*: log a
        # warning so a post-shutdown spawn (a late reconciler dispatch, a
        # racing route) is diagnosable instead of silent.
        self._closed = False

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Schedule ``coro`` as a tracked task (the ``spawn`` seam signature)."""
        if self._closed:
            logger.warning("task_spawned_after_registry_closed")
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        # Discard on completion so the set tracks only live tasks. The strong ref
        # held until here is the whole point — it stops a mid-flight task from
        # being garbage-collected and silently dropping its exception.
        task.add_done_callback(self._tasks.discard)
        return task

    def __len__(self) -> int:
        return len(self._tasks)

    async def join(self) -> None:
        """Await every live task to completion (does **not** cancel them).

        Loops so tasks spawned while draining are also awaited. Exceptions are
        tolerated (each spawned generation task records its own failure and never
        re-raises, §5.4). Used for deterministic draining in tests, and available
        as the seam a future *graceful* (drain-not-cancel) shutdown would use.
        """
        while self._tasks:
            batch = list(self._tasks)
            await asyncio.gather(*batch, return_exceptions=True)

    async def cancel_all(self) -> None:
        """Cancel every live task and await the drain (idempotent).

        Swallows the resulting ``CancelledError`` per task (that *is* the graceful
        stop) and logs any non-cancel exception a task surfaced while unwinding —
        it must not mask the shutdown. Safe to call with no live tasks.

        Marks the registry **closed**: after this returns, any further ``spawn``
        lands in a set nothing will drain again, so it logs a warning (an orphan
        spawned into a shut-down registry is a bug worth surfacing).
        """
        self._closed = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException) and not isinstance(
                result, asyncio.CancelledError
            ):
                logger.warning(
                    "generation_task_error_on_shutdown",
                    error=repr(result),
                )


class Reconciler:
    """The in-process reconciler loop (TDD §5.4 D6).

    Every ``interval_seconds`` it scans (:meth:`tick`) for paths with claimable
    work and drives ``resume_path`` for each through the registry. ``tick`` is
    public and side-effect-scoped so tests drive a single deterministic pass
    without the loop/sleep. Per-path **dedup** stops a slow resume from being
    re-dispatched every tick: a path already has an in-flight reconciler resume is
    skipped (``resume_path`` is idempotent, so this is an efficiency guard, not a
    correctness one — the atomic claim is the real coordination).
    """

    def __init__(
        self,
        orchestrator: GenerationOrchestrator,
        *,
        registry: TaskRegistry,
        interval_seconds: float,
        stale_after_seconds: float,
        session_factory: SessionFactory = new_session,
    ) -> None:
        self._orchestrator = orchestrator
        self._registry = registry
        self._interval = interval_seconds
        self._stale = stale_after_seconds
        self._session_factory = session_factory
        # path_id → the in-flight reconciler-driven resume task (dedup guard).
        self._inflight: dict[uuid.UUID, asyncio.Task[Any]] = {}

    async def run_forever(self) -> None:
        """Sleep-then-scan forever until cancelled (the lifespan's loop task).

        A tick failure (a transient DB blip in the scan) is logged and the loop
        continues — the reconciler must not die on one bad pass. ``CancelledError``
        is a ``BaseException``, so ``except Exception`` does not catch it: it
        propagates on its own and shutdown stops the loop cleanly (no explicit
        re-raise needed).
        """
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.tick()
            except Exception:
                logger.exception("reconciler_tick_failed")

    async def tick(self) -> list[uuid.UUID]:
        """One scan pass: find claimable paths, dispatch a resume for each.

        Returns the path ids dispatched *this* tick (dedup-skipped ids excluded),
        so a test can assert what a pass drove. The scan runs in its own
        short-lived session; each resume opens its own (never the scan's).
        """
        async with self._session_factory() as session:
            path_ids = await PathRepository(
                session, stale_after_seconds=self._stale
            ).ids_needing_reconciliation()

        dispatched: list[uuid.UUID] = []
        for path_id in path_ids:
            if self._dispatch(path_id):
                dispatched.append(path_id)
        return dispatched

    def _dispatch(self, path_id: uuid.UUID) -> bool:
        """Spawn a resume for ``path_id`` unless one is already in flight.

        Returns whether a resume was dispatched (``False`` = deduped).
        """
        if path_id in self._inflight:
            return False
        task = self._registry.spawn(self._orchestrator.resume_path(path_id))
        self._inflight[path_id] = task
        task.add_done_callback(lambda _task, pid=path_id: self._inflight.pop(pid, None))
        return True


class GenerationLifecycle:
    """Wires the registry + semaphore + reconciler and runs the app lifespan.

    Construct once (in the FastAPI lifespan) around the module-level
    :data:`~aleph.services.generation.generation_orchestrator`. :meth:`start`
    binds the orchestrator's runtime seams and launches the reconciler;
    :meth:`stop` reverses it. Both are idempotent enough for a clean
    startup/shutdown pair.
    """

    def __init__(
        self,
        orchestrator: GenerationOrchestrator,
        *,
        config: Settings = global_settings,
        session_factory: SessionFactory = new_session,
    ) -> None:
        self._orchestrator = orchestrator
        self._registry = TaskRegistry()
        # Constructing the semaphore needs no running loop (3.10+ binds lazily on
        # first use). It is the ``model_slot``: entering it acquires one permit.
        self._semaphore = asyncio.Semaphore(config.max_concurrent_generations)
        # D14's OWN, separate pool for Beat research — never shared with
        # ``self._semaphore`` above, so research can never queue behind (or
        # starve) lesson generation. Bound to the module-level
        # ``briefing_service`` singleton in :meth:`start`, mirroring how
        # ``self._semaphore`` is bound to ``orchestrator`` below.
        self._research_semaphore = asyncio.Semaphore(
            config.max_concurrent_brief_research
        )
        # The AL-521/AL-523 handoff gap this ticket closes: neither ticket's
        # brief owned wiring a live ``Retriever`` into production, so nothing
        # ever did — ``briefing_service`` shipped permanently bound to
        # ``_UnconfiguredRetriever`` (its loud raise), and every Beat research
        # run failed at the first retrieval step. Constructing ``ExaRetriever``
        # needs no I/O (it only stores its args, TDD D6), so — like the two
        # semaphores above — it is safe to build here, at app-assembly time.
        # ``None`` when ``EXA_API_KEY`` is unset (TDD §12: startup must still
        # succeed) — :meth:`start` then passes ``retriever=None`` through to
        # ``bind_runtime``, which leaves ``_UnconfiguredRetriever`` bound so
        # research fails visibly (§5.7) instead of silently.
        #
        # A SINGLE ``ExaRetriever`` for the whole process's lifetime is
        # correct: ``since`` (the Beat's period start) no longer lives on the
        # instance — an earlier version of this constructor took it as a
        # constructor argument, which only produced the right per-Beat date
        # filter if a fresh instance were built per Beat, and nothing did.
        # It now rides ``Retriever.search(queries, *, since)`` on every call,
        # sourced from ``QueryPlan.since`` (``retrieve()`` in
        # ``services/retrieval.py``), so one shared instance genuinely is the
        # right shape — no per-Beat construction needed here or anywhere else.
        self._research_retriever: Retriever | None = (
            ExaRetriever(
                api_key=config.exa_api_key,
                max_documents=config.brief_retrieval_max_documents,
            )
            if config.exa_api_key
            else None
        )
        self._reconciler = Reconciler(
            orchestrator,
            registry=self._registry,
            interval_seconds=config.reconciler_interval_seconds,
            stale_after_seconds=config.generation_stale_after_seconds,
            session_factory=session_factory,
        )
        self._reconciler_task: asyncio.Task[Any] | None = None

    @property
    def registry(self) -> TaskRegistry:
        return self._registry

    @property
    def reconciler(self) -> Reconciler:
        return self._reconciler

    async def start(self) -> None:
        """Bind the orchestrator's seams and start the reconciler loop.

        Rejects a double start: a second ``start`` without an intervening
        ``stop`` would launch a second reconciler loop and leak the first (its
        task handle is overwritten), and re-bind seams already bound. The lifespan
        starts exactly once, so a double start is a programming error, not a state
        to absorb.
        """
        if self._reconciler_task is not None:
            raise RuntimeError("GenerationLifecycle already started")
        self._orchestrator.bind_runtime(
            spawn=self._registry.spawn,
            model_slot=lambda: self._semaphore,
        )
        # D14: the SAME registry (background research is cancelled/self-heals
        # exactly like generation, TDD §2), but its OWN semaphore — never
        # ``self._semaphore`` — so a spike of Beat research can never queue
        # behind, or starve, lesson generation. ``retriever`` is the AL-521/
        # AL-523 handoff gap this ticket closes: a live ``ExaRetriever`` when
        # ``EXA_API_KEY`` is configured, or ``None`` (no rebind — the loud
        # ``_UnconfiguredRetriever`` stays bound) when it is not.
        briefing_service.bind_runtime(
            spawn=self._registry.spawn,
            model_slot=lambda: self._research_semaphore,
            retriever=self._research_retriever,
        )
        self._reconciler_task = asyncio.create_task(self._reconciler.run_forever())
        logger.info("generation_lifecycle_started")

    async def stop(self) -> None:
        """Graceful shutdown (§5.4). Order is load-bearing:

        1. Stop the reconciler loop first, so no new resume is dispatched into a
           registry we are about to drain (a resume spawned after the drain would
           be an orphan that survives the process).
        2. Cancel every in-flight generation task (including any resume the
           reconciler already spawned) and await the drain. Rows mid-flight stay
           ``generating`` and revert via stale recovery — nothing marks them
           failed, so they are cleanly re-claimable on the next start/poll/tick.
        3. Unbind the orchestrator's seams so the shared singleton is clean.
        """
        if self._reconciler_task is not None:
            self._reconciler_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconciler_task
            self._reconciler_task = None
        await self._registry.cancel_all()
        self._orchestrator.reset_runtime()
        briefing_service.reset_runtime()
        logger.info("generation_lifecycle_stopped")
