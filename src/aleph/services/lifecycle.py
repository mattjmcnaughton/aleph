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
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import structlog

from aleph.config import settings as global_settings
from aleph.repositories import PathRepository

# The default session factory is defined once in ``generation.py`` and shared
# here (rather than duplicated): both open a fresh short-lived session from the
# module-level maker resolved at call time (the AL-010 landmine the orchestrator
# guards against). ``_`` prefix kept — it is a package-internal seam default.
from aleph.services.generation import _default_session_factory

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable, Coroutine
    from contextlib import AbstractAsyncContextManager
    from typing import Any

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.config import Settings
    from aleph.services.generation import GenerationOrchestrator

    SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

logger = structlog.get_logger(__name__)


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
        session_factory: SessionFactory = _default_session_factory,
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
        session_factory: SessionFactory = _default_session_factory,
    ) -> None:
        self._orchestrator = orchestrator
        self._registry = TaskRegistry()
        # Constructing the semaphore needs no running loop (3.10+ binds lazily on
        # first use). It is the ``model_slot``: entering it acquires one permit.
        self._semaphore = asyncio.Semaphore(config.max_concurrent_generations)
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
        logger.info("generation_lifecycle_stopped")
