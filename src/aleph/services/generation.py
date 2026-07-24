"""Generation orchestrator: trigger + poll, prefetch chain, failure semantics.

This is the heart of Phase 1 (TDD §5.4/§5.5/§5.2). It coordinates the outline
and lesson agents through the DB state machine only — no queue, no worker
process — so any instance (or a restarted one) can pick up where another left
off. Everything here is idempotent and re-driven from the DB, so the same
``ensure_generated_through`` runs safely from a poll, a spawned task, or (later)
the reconciler.

**What lives here (AL-040) vs. what does not (AL-041).** This module owns the
generation *logic*: claiming, short transactions, the serial per-path prefetch
chain, the continuity seam (:meth:`build_prior_context`, D7), failure mapping
(§5.5), and per-call timeouts. It deliberately does **not** own the reconciler
loop, the task registry, the process-wide semaphore, or graceful-shutdown
cancellation — those are AL-041. The seam AL-041 wraps is the injected
``spawn`` callable (default :func:`asyncio.create_task`): AL-041 replaces it with
one that registers the task (a strong ref, so it is not GC'd mid-flight),
bounds it under ``MAX_CONCURRENT_GENERATIONS``, and cancels it on shutdown.

**Short-transaction discipline (load-bearing, AL-011 handoff).** A claim runs in
its own session and commits *immediately* (the claim holds a row lock and freezes
the stale clock until commit); the model call runs **outside any transaction**;
the mark runs in a fresh session. Every mark is fenced — a lost claim's mark is a
silent no-op, so a stalled worker never overwrites a fresh re-claim.

**Module-level DB access (AL-010 landmine).** Sessions come from an injected
``session_factory``. The default resolves ``db.async_session`` *through the
module* at call time (never captured at import), so the per-test database fixture
that reassigns ``db.async_session`` is honoured.

Public surface consumed by the API tickets (AL-050/051) and the reconciler
(AL-041):

* :meth:`create_path` — insert ``pending``, spawn the outline task, return the
  row (the 202 source).
* :meth:`run_outline_task` — the spawned/idempotent outline driver.
* :meth:`ensure_generated_through` / :meth:`ensure_prefetch_window` — the serial
  per-path prefetch drivers (also what a poll triggers).
* :meth:`resume_path` — outline + prefetch, the full idempotent resume.
* :meth:`build_prior_context` — the single continuity seam (D7).
* :meth:`retry_outline` / :meth:`retry_lesson` — explicit learner retries.
* :meth:`poll_path` / :meth:`poll_lesson` — poll targets (trigger + snapshot).
* :meth:`on_lesson_viewed` / :meth:`on_lesson_completed` — advance the window.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from aleph.agents.lesson import (
    LessonCaps,
    LessonDeps,
    PriorPassage,
    build_lesson_agent,
    build_lesson_prompt,
)
from aleph.agents.outline import (
    LessonOutline,
    OutlineCaps,
    OutlineDeps,
    PathOutline,
    Refusal,
    UnitOutline,
    build_outline_agent,
)
from aleph.config import settings as global_settings
from aleph.models import LessonGenerationState, Level
from aleph.repositories import (
    LessonRepository,
    PathGenerationProgress,
    PathRepository,
    QuickCheckRepository,
    UnitRepository,
)
from aleph.services.openrouter import resolve_model

if TYPE_CHECKING:
    import datetime
    import uuid
    from collections.abc import Callable, Coroutine
    from contextlib import AbstractAsyncContextManager
    from typing import Any

    from pydantic_ai.models import Model
    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.agents.lesson import LessonContent
    from aleph.agents.outline import Level as AgentLevel
    from aleph.config import Settings
    from aleph.models import Path, PathStatus

    SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
    Spawn = Callable[[Coroutine[Any, Any, Any]], Any]
    ResolveModel = Callable[[str], Model]
    # The seam AL-041 wraps around generation to enforce the process-wide
    # concurrency bound. ``model_slot()`` returns an async context manager entered
    # for the span of one claim + context load + model call — production passes
    # ``lambda: semaphore`` (an ``asyncio.Semaphore`` is itself an async CM, so
    # entering it acquires/releases one permit), tests pass one that instruments
    # concurrency, and the default is ``nullcontext`` (no bound). The permit is
    # acquired **before** the claim (§5.4): a claim implies a held permit, so a row
    # never commits ``generating`` and then queues unbounded on the semaphore
    # (which, under a spike, would let a healthy queued row's wait exceed
    # ``GENERATION_STALE_AFTER``, go stale mid-queue, and be re-claimed and
    # double-run). Permit acquisition is deliberately **outside** the per-call
    # ``asyncio.timeout`` (queue time is not model time). It spans one generation
    # and **not** the whole task: the serial per-path prefetch chain awaits its
    # sub-generations inline, acquiring and releasing one permit per lesson — a
    # whole-task bound would let a path holding a permit block on sub-work that
    # also needs one, a deadlock.
    ModelSlot = Callable[[], AbstractAsyncContextManager[Any]]

logger = structlog.get_logger(__name__)

# Learner-facing failure text (advisory 9): the ``generation_error`` column can
# surface in the lesson-view error state, so it must never carry raw provider or
# exception text (a leaked prompt, a stack detail). The specific cause is logged
# with full context instead; the stored message stays generic and actionable.
_LESSON_FAILED_MESSAGE = "Lesson generation failed. Please retry."
_LESSON_TIMEOUT_MESSAGE = "Lesson generation timed out. Please retry."

# Onboarding levels (DB enum) → the outline/lesson agents' level contract
# (``beginner | intermediate | advanced``). The agents speak a fixed vocabulary;
# the service owns the mapping (§5.1 "the service maps onboarding's levels").
_AGENT_LEVEL: dict[Level, AgentLevel] = {
    Level.NEW_TO_IT: "beginner",
    Level.SOME_EXPERIENCE: "intermediate",
    Level.WORK_IN_IT: "advanced",
}


def _default_session_factory() -> AbstractAsyncContextManager[AsyncSession]:
    """A fresh session from the module-level maker, resolved at call time.

    Reads ``db.async_session`` off the module object (not a captured value) so
    the per-test fixture's ``configure_database_url`` reassignment is seen
    (AL-010 landmine). Spawned tasks each open their own short-lived sessions
    from here — never the request's session (TDD §5.4 invariant).
    """
    from aleph import db

    return db.async_session()


@dataclass(frozen=True)
class PathStatusSnapshot:
    """A path's poll-target state: effective status + refusal + progress (§6).

    ``status`` is the **effective** status (a stale ``generating`` outline reads
    as ``failed``, so a poll shows the retry affordance, not a dead spinner).
    ``progress`` is the per-lesson generation roll-up the paths API surfaces.
    """

    status: PathStatus
    refusal_message: str | None
    progress: PathGenerationProgress


@dataclass(frozen=True)
class _LessonContext:
    """Everything one lesson generation needs, loaded from the DB in one read."""

    topic: str
    level: Level
    outline: PathOutline
    position_in_path: int
    unit_title: str
    lesson_title: str
    prior: tuple[PriorPassage, ...]


class GenerationOrchestrator:
    """Drives outline + lesson generation through the DB state machine (§5.4).

    Constructed with injectable seams so tests drive it deterministically and
    AL-041 can wrap the spawn. Defaults wire production: sessions from the
    module maker, ``asyncio.create_task`` for spawning, and the OpenRouter/stub
    resolver for models. ``config`` supplies caps, model slots, the prefetch
    window, and the per-call timeout; the caps are built from it explicitly and
    passed to the agents as run-time deps (the agents never read config).
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory = _default_session_factory,
        spawn: Spawn = asyncio.create_task,
        resolve_model_fn: ResolveModel = resolve_model,
        config: Settings = global_settings,
        prefetch_n: int | None = None,
        generation_timeout_seconds: float | None = None,
        outline_timeout_seconds: float | None = None,
        stale_after_seconds: float | None = None,
        outline_caps: OutlineCaps | None = None,
        lesson_caps: LessonCaps | None = None,
        model_slot: ModelSlot = contextlib.nullcontext,
    ) -> None:
        self._session_factory = session_factory
        self._spawn = spawn
        self._resolve_model = resolve_model_fn
        self._config = config
        # The runtime seams AL-041 rebinds in the app lifespan (see
        # :meth:`bind_runtime`): the spawn (→ a registry wrapper) and the model
        # slot (→ the process-wide semaphore). Defaults are unbound production
        # values so the orchestrator is fully usable — and testable — with no
        # lifecycle at all. The constructed values are remembered so
        # :meth:`reset_runtime` can restore them on shutdown.
        self._model_slot = model_slot
        self._base_spawn = spawn
        self._base_model_slot = model_slot
        self._prefetch_n = prefetch_n if prefetch_n is not None else config.prefetch_n
        self._timeout = (
            generation_timeout_seconds
            if generation_timeout_seconds is not None
            else config.generation_timeout_seconds
        )
        # The outline gets its own per-call budget so a test tightening the
        # lesson timeout (to force a lesson timeout deterministically) does not
        # also govern the outline run — the shared budget made the outline a CI
        # flake risk (advisory 8). Defaults to the same per-call timeout.
        self._outline_timeout = (
            outline_timeout_seconds
            if outline_timeout_seconds is not None
            else self._timeout
        )
        self._stale = (
            stale_after_seconds
            if stale_after_seconds is not None
            else config.generation_stale_after_seconds
        )
        self._outline_caps = outline_caps or _outline_caps_from(config)
        self._lesson_caps = lesson_caps or _lesson_caps_from(config)

    # -- runtime wiring (AL-041 lifespan) ---------------------------------- #

    def bind_runtime(self, *, spawn: Spawn, model_slot: ModelSlot) -> None:
        """Rebind the spawn and model-slot seams for the app's lifetime (§5.4).

        The FastAPI lifespan (``services/lifecycle.py``) calls this at startup on
        the module-level :data:`generation_orchestrator` — the same instance
        AL-050/051 import — so every route and background trigger routes through
        the task registry (strong refs, shutdown cancel) and the process-wide
        concurrency semaphore. Mutating the shared instance in place (rather than
        reconstructing and rebinding the module attribute) is deliberate: a
        rebind would not reach references AL-050 already imported.

        **AL-050, revisit this choice.** Singleton mutation-in-place is the seam
        precisely because AL-050 imports the module-level
        :data:`generation_orchestrator` directly. If AL-050 instead resolves the
        orchestrator from FastAPI app state (a DI provider) rather than importing
        the singleton, prefer constructing a fresh, already-bound orchestrator in
        the lifespan and storing *that* on ``app.state`` — no in-place mutation, no
        shared global. The executor keeps ``bind_runtime`` for now because the
        import-the-singleton access pattern is what exists today.
        """
        self._spawn = spawn
        self._model_slot = model_slot

    def reset_runtime(self) -> None:
        """Restore the unbound construction-time seams (lifespan shutdown).

        Symmetric with :meth:`bind_runtime`; keeps the module-level singleton
        clean between an app's shutdown and any later start (and between tests).
        """
        self._spawn = self._base_spawn
        self._model_slot = self._base_model_slot

    # -- repository construction ------------------------------------------- #

    def _paths(self, session: AsyncSession) -> PathRepository:
        return PathRepository(session, stale_after_seconds=self._stale)

    def _lessons(self, session: AsyncSession) -> LessonRepository:
        return LessonRepository(session, stale_after_seconds=self._stale)

    # -- path creation (POST /paths → 202) --------------------------------- #

    async def create_path(
        self, *, user_id: uuid.UUID, topic: str, level: Level
    ) -> Path:
        """Insert the path (``pending``), spawn the outline task, return the row.

        The caller (AL-050) turns the returned row into a ``202 {id}`` and
        returns immediately while the outline generates in the background. The
        spawn goes through the injected seam (AL-041 wraps it).
        """
        async with self._session_factory() as session:
            path = await self._paths(session).create(
                user_id=user_id, topic=topic, level=level
            )
            await session.commit()
            path_id = path.id

        self._spawn(self.run_outline_task(path_id))
        return path

    # -- outline task ------------------------------------------------------- #

    async def run_outline_task(
        self, path_id: uuid.UUID, *, retry: bool = False
    ) -> None:
        """Claim → run outline agent → insert units/lessons / refuse / fail.

        Idempotent and safe to call from the spawn, a poll, the reconciler, or a
        retry: the atomic claim wins at most once. On win it runs the agent
        *outside* any transaction and persists in a fresh, fenced session, then
        kicks the prefetch window (§5.4). Failure mapping is §5.5:

        * agent error / timeout → ``paths.status = failed`` (retryable)
        * ``Refusal`` output    → ``paths.status = refused`` + message, **no units**
        * ``PathOutline`` output → units/lessons inserted (``ungenerated``),
          ``paths.status = ready``, prefetch kicked
        """
        # Acquire the concurrency permit BEFORE the claim (§5.4): the permit spans
        # the claim + model call, so an outline never commits ``generating`` and
        # then waits unbounded on the semaphore — which, under a spike, would let a
        # healthy queued row's wait exceed ``GENERATION_STALE_AFTER``, go stale
        # mid-queue, and be re-claimed and double-run. Claims happen only once a
        # permit is held, so queued paths stay ``pending`` (unclaimed). The permit
        # is released before the prefetch window is kicked (below), so the serial
        # per-lesson chain never nests inside the outline's permit. Default slot is
        # a no-op; AL-041 binds the process-wide semaphore.
        async with self._model_slot():
            claimed = await self._claim_outline(path_id, retry=retry)
            if claimed is None:
                # already ready/refused, freshly generating, or lost the race
                return
            fence, topic, level = claimed

            # Top-level handler (§5.4 invariant): any escape from the claimed body
            # — a persist error, a DB blip in ``_persist_outline`` — records
            # ``failed`` best-effort rather than leaving the row wedged in
            # ``generating`` until the stale window, and never leaks an unretrieved
            # exception out of the spawned task. ``CancelledError`` (BaseException)
            # propagates for graceful shutdown; the state machine makes
            # cancellation safe (stale recovery).
            try:
                ready = await self._run_claimed_outline(path_id, fence, topic, level)
            except Exception:
                logger.exception("outline_task_failed", path_id=str(path_id))
                with contextlib.suppress(Exception):
                    await self._mark_outline_failed(path_id, fence)
                return

        if ready:
            await self.ensure_prefetch_window(path_id)

    async def _run_claimed_outline(
        self, path_id: uuid.UUID, fence: datetime.datetime, topic: str, level: Level
    ) -> bool:
        """Run the outline agent under the claim and persist; return whether the
        path is now ``ready`` (so the caller kicks the prefetch window).

        Maps provider error/timeout → ``failed`` and a ``Refusal`` → ``refused``
        (§5.5); returns ``False`` for both (nothing to prefetch), and ``False`` on
        a lost fence (a re-claim owns the units). Only a persisted ``PathOutline``
        returns ``True``.
        """
        agent = build_outline_agent()
        deps = OutlineDeps(level=_AGENT_LEVEL[level], caps=self._outline_caps)
        model = self._resolve_model(self._config.model_outline)
        try:
            # The concurrency permit is already held by ``run_outline_task``
            # (acquired before the claim, §5.4, so queue time never counts against
            # the per-call budget); here we only bound the model call itself.
            async with asyncio.timeout(self._outline_timeout):
                run = await agent.run(topic, deps=deps, model=model)
        except TimeoutError:
            await self._mark_outline_failed(path_id, fence)
            return False
        except Exception:  # noqa: BLE001 - §5.5: any provider error maps to failed
            logger.exception("outline_generation_failed", path_id=str(path_id))
            await self._mark_outline_failed(path_id, fence)
            return False

        output = run.output
        if isinstance(output, Refusal):
            await self._mark_outline_refused(path_id, fence, output.message)
            return False

        return await self._persist_outline(path_id, fence, output)

    async def _claim_outline(
        self, path_id: uuid.UUID, *, retry: bool
    ) -> tuple[datetime.datetime, str, Level] | None:
        async with self._session_factory() as session:
            repo = self._paths(session)
            path = await repo.get(path_id)
            if path is None:
                return None
            claim = repo.claim_outline_for_retry if retry else repo.claim_outline
            fence = await claim(path_id)
            topic, level = path.topic, path.level
            await session.commit()
        if fence is None:
            return None
        return fence, topic, level

    @staticmethod
    async def _commit_or_rollback(session: AsyncSession, *, ok: bool) -> None:
        """Commit a fenced mark that still owned its claim; roll back a lost one.

        The three best-effort marks (:meth:`_mark_outline_failed`,
        :meth:`_mark_outline_refused`, :meth:`_mark_lesson_failed`) share this
        tail: a lost fence is a silent no-op (roll back the empty unit of work),
        a won one commits (ponytail).
        """
        if ok:
            await session.commit()
        else:
            await session.rollback()

    async def _mark_outline_failed(
        self, path_id: uuid.UUID, fence: datetime.datetime
    ) -> None:
        async with self._session_factory() as session:
            ok = await self._paths(session).mark_failed(path_id, fence=fence)
            await self._commit_or_rollback(session, ok=ok)

    async def _mark_outline_refused(
        self, path_id: uuid.UUID, fence: datetime.datetime, message: str
    ) -> None:
        async with self._session_factory() as session:
            ok = await self._paths(session).mark_refused(
                path_id=path_id, message=message, fence=fence
            )
            await self._commit_or_rollback(session, ok=ok)

    async def _persist_outline(
        self, path_id: uuid.UUID, fence: datetime.datetime, outline: PathOutline
    ) -> bool:
        """Mark ready then insert units + lessons, all under the claim fence.

        The ready-mark runs **first** in the transaction: a lost fence (a stale
        re-claim slipped in) makes ``mark_ready`` return ``False``, so we roll
        back before inserting anything — the loser drops silently instead of
        hitting a ``UNIQUE(path_id, position)`` IntegrityError against the
        winner's already-inserted units (advisory 3). On a won fence the mark and
        the inserts commit atomically, so no reader sees ``ready`` without units.
        """
        async with self._session_factory() as session:
            marked = await self._paths(session).mark_ready(path_id, fence=fence)
            if not marked:
                await session.rollback()
                return False
            units_repo = UnitRepository(session)
            lessons_repo = self._lessons(session)
            position_in_path = 0
            for unit_index, unit in enumerate(outline.units, start=1):
                unit_row = await units_repo.create(
                    path_id=path_id,
                    position=unit_index,
                    title=unit.title,
                    summary=unit.summary,
                )
                for lesson_index, lesson in enumerate(unit.lessons, start=1):
                    position_in_path += 1
                    await lessons_repo.create(
                        unit_id=unit_row.id,
                        path_id=path_id,
                        position_in_path=position_in_path,
                        position_in_unit=lesson_index,
                        title=lesson.title,
                    )
            await session.commit()
            return True

    # -- prefetch chain ----------------------------------------------------- #

    async def ensure_prefetch_window(self, path_id: uuid.UUID) -> None:
        """Recompute the window and ensure it, the idempotent hook for every
        trigger (path ready, lesson viewed, lesson completed, retry, poll).

        Window ``k = first_incomplete_position + PREFETCH_N`` (§5.4). No lessons
        yet (outline pending) or a fully-complete path yields no window and no-ops.
        """
        async with self._session_factory() as session:
            first = await self._lessons(session).first_incomplete(path_id)
        if first is None:
            return
        await self.ensure_generated_through(
            path_id, first.position_in_path + self._prefetch_n
        )

    async def ensure_generated_through(
        self, path_id: uuid.UUID, through_position: int
    ) -> None:
        """Generate lessons serially in order up to ``through_position`` (§5.4).

        Walks ``position_in_path`` ascending. A ``generated`` lesson is skipped;
        a ``failed`` one **stops the chain** (never auto-retried — the learner's
        explicit retry is the only loop that re-runs a real failure); an
        ``ungenerated`` or stale ``generating`` one is the next target. The
        atomic claim distinguishes a stale row (re-claimable, self-healing) from
        a fresh ``generating`` one owned by a concurrent worker — so this is safe
        under interleaved polls: exactly one worker generates each lesson, in
        order (the ordering invariant, by construction).
        """
        while True:
            target = await self._next_target(path_id, through_position)
            if target is None:
                return
            # Auto claim: re-claims a stale ``generating`` row (crash recovery)
            # but never a real ``failed`` one — prefetch auto-recovers crashes,
            # it never retry-burns a genuine error (§5.4; that is the learner's
            # explicit :meth:`retry_lesson`).
            generated = await self._claim_and_generate(path_id, target, retry=False)
            if not generated:
                # Lost the claim (a concurrent worker owns it) or the generation
                # failed — either way this chain stops; the owner (or the next
                # poll / retry) carries it forward.
                return

    async def _next_target(
        self, path_id: uuid.UUID, through_position: int
    ) -> uuid.UUID | None:
        """The next lesson to generate, or ``None`` if the window is filled/blocked.

        Uses the **raw** generation state (not the effective one): a stale
        ``generating`` row must remain a claim target (self-healing), whereas the
        effective state collapses it to ``failed``. Only a *real* ``failed`` stops
        the walk.
        """
        async with self._session_factory() as session:
            lessons = await self._lessons(session).list_for_path(path_id)
        for lesson in lessons:
            if lesson.position_in_path > through_position:
                return None
            state = lesson.generation_state
            if state is LessonGenerationState.GENERATED:
                continue
            if state is LessonGenerationState.FAILED:
                return None  # a real failure stops the chain (§5.4)
            return lesson.id  # ungenerated, or (maybe stale) generating
        return None

    async def _claim_and_generate(
        self, path_id: uuid.UUID, lesson_id: uuid.UUID, *, retry: bool
    ) -> bool:
        """Claim one lesson, generate it, and mark the result. Returns whether it
        is now ``generated`` by *this* call so the chain may advance.

        The concurrency permit is acquired **before** the claim (§5.4): the permit
        spans the claim + context load + model call, so a lesson never commits
        ``generating`` and then waits unbounded on the semaphore. Were the claim to
        run first, under a spike (e.g. 50 paths, 8 permits) a healthy queued row's
        wait for a permit could exceed ``GENERATION_STALE_AFTER``, going stale
        mid-queue, getting re-claimed, and running the model twice per row.
        Acquiring the permit first means claims happen only once a permit is held —
        the queued rows stay ``ungenerated`` (unclaimed), not ``generating``. The
        permit is released per-lesson before the chain advances, so the serial
        per-path walk never holds two permits (no self-deadlock). Default slot is a
        no-op; AL-041 binds the process-wide semaphore.
        """
        async with self._model_slot():
            # 1. Claim (own short transaction, commit immediately) — under the
            # permit, so a committed claim implies a held permit.
            async with self._session_factory() as session:
                repo = self._lessons(session)
                claim = repo.claim_for_retry if retry else repo.claim_for_generation
                fence = await claim(lesson_id)
                await session.commit()
            if fence is None:
                return False  # someone else holds it, or it is terminal

            # 2. Everything after the claim runs under a top-level handler (§5.4
            # invariant): any escape — a context-load error, a persist blip —
            # records ``failed`` under the fence best-effort, so the row never
            # wedges in ``generating`` until the stale window and the spawned task
            # never leaks an unretrieved exception. ``CancelledError``
            # (BaseException) propagates for graceful shutdown; the state machine
            # makes cancellation safe.
            try:
                return await self._run_claimed_lesson(path_id, lesson_id, fence)
            except Exception:
                logger.exception("lesson_task_failed", lesson_id=str(lesson_id))
                with contextlib.suppress(Exception):
                    await self._mark_lesson_failed(
                        lesson_id, fence, _LESSON_FAILED_MESSAGE
                    )
                return False

    async def _run_claimed_lesson(
        self, path_id: uuid.UUID, lesson_id: uuid.UUID, fence: datetime.datetime
    ) -> bool:
        """Load context, run the lesson agent OUTSIDE any transaction, persist —
        the body the claim fences and :meth:`_claim_and_generate` guards."""
        async with self._session_factory() as session:
            context = await self._load_lesson_context(session, path_id, lesson_id)
        if context is None:
            # A vanished lesson/unit is referential breakage, not a transient
            # error: record failed (generic message; detail logged).
            logger.warning(
                "lesson_context_unavailable",
                lesson_id=str(lesson_id),
                path_id=str(path_id),
            )
            await self._mark_lesson_failed(lesson_id, fence, _LESSON_FAILED_MESSAGE)
            return False

        deps = LessonDeps(
            topic=context.topic,
            level=_AGENT_LEVEL[context.level],
            outline=context.outline,
            position_in_path=context.position_in_path,
            unit_title=context.unit_title,
            lesson_title=context.lesson_title,
            prior_passages=context.prior,
            caps=self._lesson_caps,
        )
        model = self._resolve_model(self._config.model_lesson)
        try:
            # The concurrency permit is already held by ``_claim_and_generate``
            # (acquired before the claim, §5.4, and released once per lesson so the
            # serial chain never holds two); here we only bound the model call.
            async with asyncio.timeout(self._timeout):
                run = await build_lesson_agent().run(
                    build_lesson_prompt(deps), deps=deps, model=model
                )
        except TimeoutError:
            await self._mark_lesson_failed(lesson_id, fence, _LESSON_TIMEOUT_MESSAGE)
            return False
        except Exception:  # noqa: BLE001 - §5.5: any error → failed + recorded
            # The stored message stays generic (advisory 9): raw provider text may
            # carry prompt/stack detail and reaches a learner-visible field. Log
            # the real cause with full context here.
            logger.exception("lesson_generation_failed", lesson_id=str(lesson_id))
            await self._mark_lesson_failed(lesson_id, fence, _LESSON_FAILED_MESSAGE)
            return False

        # Persist content + quick check in a fresh, fenced transaction.
        return await self._persist_lesson(lesson_id, fence, run.output)

    async def _persist_lesson(
        self, lesson_id: uuid.UUID, fence: datetime.datetime, content: LessonContent
    ) -> bool:
        async with self._session_factory() as session:
            marked = await self._lessons(session).mark_generated(
                lesson_id=lesson_id, read_passage=content.read_passage, fence=fence
            )
            if not marked:
                await session.rollback()
                return False  # lost the claim to a re-claim; drop silently
            quick_check = content.quick_check
            await QuickCheckRepository(session).create(
                lesson_id=lesson_id,
                stem=quick_check.stem,
                options=quick_check.options,
                correct_index=quick_check.correct_index,
                explanation=quick_check.explanation,
            )
            await session.commit()
            return True

    async def _mark_lesson_failed(
        self, lesson_id: uuid.UUID, fence: datetime.datetime, error: str
    ) -> None:
        async with self._session_factory() as session:
            ok = await self._lessons(session).mark_failed(
                lesson_id=lesson_id, error=error, fence=fence
            )
            await self._commit_or_rollback(session, ok=ok)

    # -- continuity seam (D7) ---------------------------------------------- #

    async def build_prior_context(
        self, session: AsyncSession, path_id: uuid.UUID, position_in_path: int
    ) -> tuple[PriorPassage, ...]:
        """The single continuity seam (D7/§5.2): prior Read passages for a lesson.

        Uses ``LessonRepository.generated_passages_before`` — only already
        ``generated`` passages, ascending, so lesson N's context is exactly
        lessons ``1…N-1`` (the ordering invariant guarantees they are complete and
        immutable). The repo returns each passage's real unit **and** lesson title
        (joined from ``units``), so ``PriorPassage``'s prompt prefix places each
        passage in the path (``[Unit / Lesson]``, §5.2) — the passage *text*
        carries continuity, the titles are the locator. This is the sole place the
        upgrade path (running summary / retrieval, deferred D7) would slot in.
        """
        triples = await self._lessons(session).generated_passages_before(
            path_id=path_id, position_in_path=position_in_path
        )
        return tuple(
            PriorPassage(
                unit_title=unit_title,
                lesson_title=lesson_title,
                read_passage=passage,
            )
            for unit_title, lesson_title, passage in triples
        )

    async def _load_lesson_context(
        self, session: AsyncSession, path_id: uuid.UUID, lesson_id: uuid.UUID
    ) -> _LessonContext | None:
        path = await self._paths(session).get(path_id)
        lesson = await self._lessons(session).get(lesson_id)
        if path is None or lesson is None:
            return None
        # A missing unit is referential breakage (a lesson always belongs to a
        # unit). Return ``None`` so the caller records ``failed`` — the same
        # branch as a vanished lesson — rather than silently generating with an
        # empty unit title that would hide the breakage (advisory 11). Routed
        # through the repository, not a raw ``session.get`` (advisory 12).
        unit = await UnitRepository(session).get(lesson.unit_id)
        if unit is None:
            return None
        outline = await self._load_outline(session, path_id)
        prior = await self.build_prior_context(
            session, path_id, lesson.position_in_path
        )
        return _LessonContext(
            topic=path.topic,
            level=path.level,
            outline=outline,
            position_in_path=lesson.position_in_path,
            unit_title=unit.title,
            lesson_title=lesson.title,
            prior=prior,
        )

    async def _load_outline(
        self, session: AsyncSession, path_id: uuid.UUID
    ) -> PathOutline:
        """Reconstruct the ``PathOutline`` (titles only) from the persisted tree.

        The lesson agent needs whole-path context (§5.1); it lives in the DB as
        units + lessons, so rebuild the titles-only shape the agent's prompt
        serializes.
        """
        units = await UnitRepository(session).list_for_path(path_id)
        lessons = await self._lessons(session).list_for_path(path_id)
        by_unit: dict[uuid.UUID, list[Any]] = defaultdict(list)
        for lesson in lessons:
            by_unit[lesson.unit_id].append(lesson)
        unit_outlines = [
            UnitOutline(
                title=unit.title,
                summary=unit.summary,
                lessons=[
                    LessonOutline(title=lesson.title)
                    for lesson in sorted(
                        by_unit[unit.id], key=lambda row: row.position_in_unit
                    )
                ],
            )
            for unit in units
        ]
        return PathOutline(units=unit_outlines)

    # -- retries (explicit learner loops) ---------------------------------- #

    async def retry_outline(self, path_id: uuid.UUID) -> None:
        """Re-run a ``failed`` outline (POST /paths/{id}/retry, §5.5).

        Uses the retry claim (re-claims ``failed``), then drives generation.
        """
        await self.run_outline_task(path_id, retry=True)

    async def retry_lesson(self, lesson_id: uuid.UUID) -> None:
        """Re-run a ``failed`` lesson, resume the chain (POST /lessons/{id}/generate).

        The ordering invariant (§5.2 / PRD §5.2) binds retry too: a lesson may
        (re)generate only when every predecessor is ``generated`` — otherwise it
        would generate out of order with incomplete, and permanently-frozen
        (``generated`` is terminal/immutable), prior context. So an explicit retry
        re-runs the lesson **only when it is the current chain head** (the first
        non-``generated`` lesson — i.e. all predecessors are ``generated``). The
        auto walk stops at a real ``failed`` head; this is the one loop allowed to
        re-claim it (via the retry claim). If the lesson is not the head, the retry
        does not touch it and falls through to a plain prefetch that advances the
        chain in order. On success the prefetch window is refilled, so generation
        resumes past the previously-blocking lesson (§5.4/§5.5).

        AL-051's endpoint stays simple: it resolves the lesson id and calls this —
        the ordering guard lives here, not in the route.
        """
        path_id = await self._path_of_lesson(lesson_id)
        if path_id is None:
            return
        if await self._chain_head(path_id) == lesson_id:
            await self._claim_and_generate(path_id, lesson_id, retry=True)
        await self.ensure_prefetch_window(path_id)

    async def _chain_head(self, path_id: uuid.UUID) -> uuid.UUID | None:
        """The first non-``generated`` lesson (ungenerated, failed, or generating),
        or ``None`` when the whole path is generated.

        This is the lesson the serial prefetch walk would act on next. Retry uses
        it to refuse an out-of-order re-generation: only the head has all its
        predecessors ``generated`` (and ``generated`` is immutable, so a head that
        reads clear stays clear — the gate is sound under concurrency; the atomic
        claim is the real writer).
        """
        async with self._session_factory() as session:
            lessons = await self._lessons(session).list_for_path(path_id)
        for lesson in lessons:
            if lesson.generation_state is not LessonGenerationState.GENERATED:
                return lesson.id
        return None

    # -- poll targets & window-advancing hooks (AL-050/051) ---------------- #

    async def poll_path(self, path_id: uuid.UUID) -> PathStatusSnapshot | None:
        """Poll target for ``GET /paths/{id}``: trigger a resume, return a snapshot.

        Poll-as-trigger (§5.4): the poll spawns the same idempotent resume
        (outline + prefetch) so a chain lost to a crash resumes within one poll,
        then returns the current effective status + progress. ``None`` if the path
        does not exist.
        """
        self._spawn(self.resume_path(path_id))
        return await self._path_snapshot(path_id)

    async def poll_lesson(self, lesson_id: uuid.UUID) -> LessonGenerationState | None:
        """Poll target for ``GET /lessons/{id}``: trigger a resume, return the state.

        Reports the lesson's **effective** generation state (stale ``generating``
        → ``failed``) and spawns a resume so a wedged chain self-heals.
        """
        path_id = await self._path_of_lesson(lesson_id)
        if path_id is None:
            return None
        self._spawn(self.resume_path(path_id))
        async with self._session_factory() as session:
            return await self._lessons(session).effective_state(lesson_id)

    async def on_lesson_viewed(self, lesson_id: uuid.UUID) -> None:
        """Advance the prefetch window when a lesson is viewed (§5.4)."""
        await self._trigger_window_for_lesson(lesson_id)

    async def on_lesson_completed(self, lesson_id: uuid.UUID) -> None:
        """Advance the prefetch window when a lesson is completed (§5.4).

        Completion moves ``first_incomplete`` forward, so the window slides; this
        fires the prefetch of the newly-in-window lessons. The caller (AL-051)
        owns persisting the completion itself.

        AL-051 note (read-before-commit race): call this **after** the completion
        is committed, so ``first_incomplete`` — recomputed here in a fresh session
        — already reflects it. If it is called mid-transaction the window is one
        lesson short for that trigger; the window is idempotent and every later
        trigger (the next poll, view, or completion) re-fills it, so the race
        self-heals — but committing first avoids the extra round trip.
        """
        await self._trigger_window_for_lesson(lesson_id)

    async def _trigger_window_for_lesson(self, lesson_id: uuid.UUID) -> None:
        path_id = await self._path_of_lesson(lesson_id)
        if path_id is not None:
            self._spawn(self.ensure_prefetch_window(path_id))

    async def resume_path(self, path_id: uuid.UUID) -> None:
        """The full idempotent resume a poll/reconciler triggers: outline + prefetch.

        ``run_outline_task`` claims a ``pending`` or stale ``generating`` outline
        (a no-op on ``ready``/``refused``/fresh-``generating``), then the prefetch
        window is (re)filled. Safe to call repeatedly and concurrently.
        """
        await self.run_outline_task(path_id)
        await self.ensure_prefetch_window(path_id)

    # -- snapshots ---------------------------------------------------------- #

    async def _path_snapshot(self, path_id: uuid.UUID) -> PathStatusSnapshot | None:
        async with self._session_factory() as session:
            repo = self._paths(session)
            path = await repo.get(path_id)
            if path is None:
                return None
            status = await repo.effective_status(path_id)
            if status is None:
                # Raced a concurrent delete between the two reads: report gone,
                # not a 500 (advisory 5) — the row existed a moment ago.
                return None
            summaries = await self._lessons(session).progress_summaries([path_id])
            return PathStatusSnapshot(
                status=status,
                refusal_message=path.refusal_message,
                progress=summaries[path_id],
            )

    async def _path_of_lesson(self, lesson_id: uuid.UUID) -> uuid.UUID | None:
        async with self._session_factory() as session:
            lesson = await self._lessons(session).get(lesson_id)
            return lesson.path_id if lesson is not None else None


# --- caps construction (from Settings, explicit — the agents never read config) --


def _outline_caps_from(config: Settings) -> OutlineCaps:
    return OutlineCaps(
        units_target=config.outline_units_target,
        max_units=config.max_units,
        lessons_per_unit_min=config.lessons_per_unit_min,
        lessons_per_unit_max=config.lessons_per_unit_max,
        max_lessons_per_path=config.max_lessons_per_path,
    )


def _lesson_caps_from(config: Settings) -> LessonCaps:
    return LessonCaps(
        passage_words_min=config.read_passage_words_min,
        passage_words_max=config.read_passage_words_max,
    )


# A module-level default instance for the API layer to import (AL-050/051). It
# wires production defaults (module sessions, ``asyncio.create_task`` spawn, the
# OpenRouter/stub resolver); AL-041 will replace the spawn with its registry +
# semaphore wrapper. Constructing it is pure (no I/O, no network).
generation_orchestrator = GenerationOrchestrator()
