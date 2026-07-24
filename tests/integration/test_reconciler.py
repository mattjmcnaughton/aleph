"""Integration tests for the reconciler, concurrency bound, and shutdown (AL-041).

Against real Postgres with a controllable model at the resolver seam (fakes over
mocks) — the four ticket acceptance criteria, TDD §5.4:

1. **Mid-flight kill recovered by the reconciler** within one tick + stale
   timeout: a stale ``generating`` lesson (a crashed run) and a ``pending`` path
   with no live task both drain on one :meth:`Reconciler.tick`.
2. **The semaphore caps concurrent model calls** — observable via an instrumented
   model that records peak concurrency.
3. **Shutdown with in-flight work leaves rows re-claimable** — cancellation
   propagates (no failed-mark, no cleanup logic), the row stays ``generating`` and
   is re-claimable via stale recovery.
4. **A task raising an unexpected exception records ``failed``** — the top-level
   handler catches any ``Exception`` and the registry drains cleanly, nothing
   leaked.

Determinism: events/barriers, not sleeps, gate concurrency; stale windows are
shrunk via injected settings rather than waited out.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from aleph import db
from aleph.models import Lesson, LessonGenerationState, Level, Path, PathStatus
from aleph.repositories import LessonRepository
from aleph.services.generation import GenerationOrchestrator
from aleph.services.lifecycle import GenerationLifecycle, Reconciler, TaskRegistry
from aleph.services.stub_model import (
    _build_lesson as stub_build_lesson,
)
from aleph.services.stub_model import (
    _build_outline as stub_build_outline,
)
from aleph.services.stub_model import (
    _clean_topic as stub_clean_topic,
)
from aleph.services.stub_model import (
    _read_position as stub_read_position,
)
from aleph.services.stub_model import (
    _tool_with as stub_tool_with,
)
from aleph.services.stub_model import (
    _user_text as stub_user_text,
)

from .conftest import create_user
from .test_generation import (
    _generated_lessons,
    _lesson_state,
    _reload_path,
    _seed_path_with_lessons,
    stub_resolver,
)

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable, Coroutine
    from contextlib import AbstractAsyncContextManager
    from typing import Any

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model
    from pydantic_ai.models.function import AgentInfo
    from sqlalchemy.ext.asyncio import AsyncSession


def _session_factory() -> AsyncSession:
    return db.async_session()


def _lesson_reply(text: str, tool_name: str) -> ModelResponse:
    topic = stub_clean_topic(text) or "the topic"
    content = stub_build_lesson(topic, stub_read_position(text) or 1)
    return ModelResponse(
        parts=[ToolCallPart(tool_name=tool_name, args=content.model_dump())]
    )


def _outline_reply(text: str, tool_name: str) -> ModelResponse:
    topic = stub_clean_topic(text) or "the topic"
    outline = stub_build_outline(topic)
    return ModelResponse(
        parts=[ToolCallPart(tool_name=tool_name, args=outline.model_dump())]
    )


def _build_orchestrator(
    *,
    resolve_model_fn: Callable[[str], Model],
    spawn: Callable[[Coroutine[Any, Any, Any]], Any],
    stale_after_seconds: float,
    prefetch_n: int = 2,
    model_slot: Callable[[], AbstractAsyncContextManager[Any]] = contextlib.nullcontext,
) -> GenerationOrchestrator:
    return GenerationOrchestrator(
        session_factory=_session_factory,
        spawn=spawn,
        resolve_model_fn=resolve_model_fn,
        generation_timeout_seconds=30.0,
        outline_timeout_seconds=30.0,
        stale_after_seconds=stale_after_seconds,
        prefetch_n=prefetch_n,
        model_slot=model_slot,
    )


# --------------------------------------------------------------------------- #
# Criterion 1: kill mid-flight → recovered by the reconciler on one tick
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_reconciler_recovers_stale_generating_lesson_on_one_tick() -> None:
    # Simulate a crashed generation: lesson 1 wedged in `generating` with a
    # backdated stamp (older than the SHRUNK stale window). One reconciler tick
    # must re-claim and complete it — no learner poll needed (§5.4 D6).
    stale = 0.05
    registry = TaskRegistry()
    orch = _build_orchestrator(
        resolve_model_fn=stub_resolver(),
        spawn=registry.spawn,
        stale_after_seconds=stale,
    )
    reconciler = Reconciler(
        orch,
        registry=registry,
        interval_seconds=999,  # loop never fires; we drive tick() directly
        stale_after_seconds=stale,
        session_factory=_session_factory,
    )

    path_id, ids = await _seed_path_with_lessons(
        [
            (1, LessonGenerationState.UNGENERATED),
            (2, LessonGenerationState.UNGENERATED),
        ]
    )
    # Wedge lesson 1 in a stale `generating` (a crashed run).
    async with db.async_session() as session:
        lesson = await session.get(Lesson, ids[1])
        assert lesson is not None
        lesson.generation_state = LessonGenerationState.GENERATING
        lesson.generation_started_at = datetime.now(UTC) - timedelta(seconds=10)
        await session.commit()

    dispatched = await reconciler.tick()
    assert path_id in dispatched
    await registry.join()

    assert await _lesson_state(ids[1]) is LessonGenerationState.GENERATED


@pytest.mark.anyio
async def test_reconciler_recovers_pending_path_with_no_live_task() -> None:
    # A path stuck `pending` because its outline task was lost to a crash/deploy
    # (nothing live holds it). The reconciler picks it up and drives outline +
    # prefetch to `ready` (§5.4 D6: work with no active poller drains on its own).
    stale = 0.05
    registry = TaskRegistry()
    orch = _build_orchestrator(
        resolve_model_fn=stub_resolver(),
        spawn=registry.spawn,
        stale_after_seconds=stale,
        prefetch_n=2,
    )
    reconciler = Reconciler(
        orch,
        registry=registry,
        interval_seconds=999,
        stale_after_seconds=stale,
        session_factory=_session_factory,
    )

    async with db.async_session() as session:
        user = await create_user(session)
        path = Path(
            user_id=user.id,
            topic="Abandoned pending topic",
            level=Level.SOME_EXPERIENCE,
            status=PathStatus.PENDING,
        )
        session.add(path)
        await session.flush()
        path_id = path.id
        await session.commit()

    dispatched = await reconciler.tick()
    assert path_id in dispatched
    await registry.join()

    reloaded = await _reload_path(path_id)
    assert reloaded.status is PathStatus.READY
    generated = [
        lesson
        for lesson in await _generated_lessons(path_id)
        if lesson.generation_state is LessonGenerationState.GENERATED
    ]
    assert generated  # prefetch produced content


@pytest.mark.anyio
async def test_reconciler_dedups_inflight_resume_for_same_path() -> None:
    # A second tick while a path's resume is still in flight must not dispatch a
    # duplicate (efficiency guard; resume_path is idempotent regardless).
    stale = 0.05
    registry = TaskRegistry()

    started = asyncio.Event()
    release = asyncio.Event()

    def resolver() -> Callable[[str], Model]:
        async def respond(
            messages: list[ModelMessage], info: AgentInfo
        ) -> ModelResponse:
            text = stub_user_text(messages)
            lesson_tool = stub_tool_with(info.output_tools, "read_passage")
            if lesson_tool is not None:
                started.set()
                await release.wait()  # hold the first resume in flight
                return _lesson_reply(text, lesson_tool.name)
            outline_tool = stub_tool_with(info.output_tools, "units")
            assert outline_tool is not None
            return _outline_reply(text, outline_tool.name)

        model = FunctionModel(respond)
        return lambda _id: model

    orch = _build_orchestrator(
        resolve_model_fn=resolver(),
        spawn=registry.spawn,
        stale_after_seconds=stale,
        prefetch_n=1,
    )
    reconciler = Reconciler(
        orch,
        registry=registry,
        interval_seconds=999,
        stale_after_seconds=stale,
        session_factory=_session_factory,
    )

    path_id, ids = await _seed_path_with_lessons(
        [(1, LessonGenerationState.UNGENERATED)]
    )

    first = await reconciler.tick()
    assert path_id in first
    await asyncio.wait_for(started.wait(), timeout=1.0)  # resume is now in flight

    second = await reconciler.tick()
    assert path_id not in second  # deduped

    release.set()
    await registry.join()
    assert await _lesson_state(ids[1]) is LessonGenerationState.GENERATED


# --------------------------------------------------------------------------- #
# Criterion 2: the semaphore caps concurrent model calls (instrumented model)
# --------------------------------------------------------------------------- #


class ConcurrencyProbe:
    """An instrumented model recording peak *concurrent* lesson model calls.

    Each lesson call increments a live counter (tracking the peak) and blocks on
    a release event, so a test can observe how many calls are simultaneously
    inside the model — i.e. how many permits the semaphore admitted. Outline
    calls pass through un-instrumented (only lesson calls are driven here).
    """

    def __init__(self, *, target: int) -> None:
        self.current = 0
        self.peak = 0
        self._target = target
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

    def resolver(self) -> Callable[[str], Model]:
        async def respond(
            messages: list[ModelMessage], info: AgentInfo
        ) -> ModelResponse:
            text = stub_user_text(messages)
            lesson_tool = stub_tool_with(info.output_tools, "read_passage")
            if lesson_tool is None:
                outline_tool = stub_tool_with(info.output_tools, "units")
                assert outline_tool is not None
                return _outline_reply(text, outline_tool.name)
            self.current += 1
            self.peak = max(self.peak, self.current)
            if self.current >= self._target:
                self.reached.set()
            try:
                await self.release.wait()
                return _lesson_reply(text, lesson_tool.name)
            finally:
                self.current -= 1

        model = FunctionModel(respond)
        return lambda _id: model


async def _seed_ready_paths_one_lesson(count: int) -> list[uuid.UUID]:
    """`count` ready paths, each a distinct user with one ungenerated lesson.

    Distinct usernames per path (``_seed_path_with_lessons`` reuses one default
    username, which collides on the unique constraint across several paths).
    """
    from aleph.models import Unit

    ids: list[uuid.UUID] = []
    async with db.async_session() as session:
        for index in range(count):
            user = await create_user(session, username=f"probe-user-{index}")
            path = Path(
                user_id=user.id,
                topic=f"Concurrency topic {index}",
                level=Level.SOME_EXPERIENCE,
                status=PathStatus.READY,
            )
            session.add(path)
            await session.flush()
            unit = Unit(path=path, position=1, title="Unit 1", summary="s")
            session.add(unit)
            await session.flush()
            session.add(
                Lesson(
                    unit=unit,
                    path=path,
                    position_in_path=1,
                    position_in_unit=1,
                    title="Lesson 1",
                    generation_state=LessonGenerationState.UNGENERATED,
                )
            )
            ids.append(path.id)
        await session.commit()
    return ids


@pytest.mark.anyio
async def test_semaphore_caps_concurrent_model_calls() -> None:
    limit = 3
    demand = 6
    semaphore = asyncio.Semaphore(limit)
    probe = ConcurrencyProbe(target=limit)
    orch = _build_orchestrator(
        resolve_model_fn=probe.resolver(),
        spawn=asyncio.create_task,
        stale_after_seconds=180,
        model_slot=lambda: semaphore,
    )

    path_ids = await _seed_ready_paths_one_lesson(demand)
    # Fire all `demand` lesson generations at once; the bound must let only
    # `limit` be inside the model simultaneously.
    runners = [
        asyncio.create_task(orch.ensure_generated_through(path_id, 1))
        for path_id in path_ids
    ]

    # Wait until the cap is reached, then assert it is never exceeded. The
    # semaphore strictly caps entries, so `current` cannot pass `limit` — the
    # other `demand - limit` calls are queued on the permit, not inside the model.
    await asyncio.wait_for(probe.reached.wait(), timeout=2.0)
    assert probe.current == limit
    assert probe.peak == limit

    probe.release.set()
    await asyncio.gather(*runners)

    assert probe.peak == limit  # never exceeded the bound
    for path_id in path_ids:
        lessons = await _generated_lessons(path_id)
        assert all(
            le.generation_state is LessonGenerationState.GENERATED for le in lessons
        )


@pytest.mark.anyio
async def test_without_bound_all_calls_run_concurrently() -> None:
    # Contrast: with the default (no bound) all `demand` calls enter the model at
    # once — proving the previous test's cap is the semaphore, not some other
    # serialization.
    demand = 6
    probe = ConcurrencyProbe(target=demand)
    orch = _build_orchestrator(
        resolve_model_fn=probe.resolver(),
        spawn=asyncio.create_task,
        stale_after_seconds=180,
        # model_slot defaults to nullcontext → unbounded
    )

    path_ids = await _seed_ready_paths_one_lesson(demand)
    runners = [
        asyncio.create_task(orch.ensure_generated_through(path_id, 1))
        for path_id in path_ids
    ]

    await asyncio.wait_for(probe.reached.wait(), timeout=2.0)
    assert probe.current == demand
    assert probe.peak == demand

    probe.release.set()
    await asyncio.gather(*runners)


# --------------------------------------------------------------------------- #
# Review item 1: the permit is acquired BEFORE the claim (no claim-before-permit)
# --------------------------------------------------------------------------- #


class _GatedSlot:
    """A model-slot that signals when a *second* acquisition is attempted.

    Wraps a semaphore. ``second_reached`` fires the moment a second caller enters
    the slot — i.e. reaches the permit gate. Because the orchestrator enters the
    slot *before* the claim (the fix under test), the second caller reaching the
    gate has provably NOT yet claimed while the first holds the only permit. (With
    the old claim-before-permit order the slot was entered only *after* claiming,
    so the second caller would already be ``generating`` when it reached here.)
    """

    def __init__(self, permits: int) -> None:
        self._sem = asyncio.Semaphore(permits)
        self._attempts = 0
        self.second_reached = asyncio.Event()

    def __call__(self) -> _GatedSlot:
        return self

    async def __aenter__(self) -> _GatedSlot:
        self._attempts += 1
        if self._attempts >= 2:
            self.second_reached.set()
        await self._sem.acquire()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self._sem.release()


@pytest.mark.anyio
async def test_permit_acquired_before_claim_leaves_queued_row_unclaimed() -> None:
    # With one permit and a wedged first model call, the second path's lesson must
    # stay UNGENERATED (unclaimed, generation_started_at unset) while it queues on
    # the permit — a claim happens ONLY once a permit is held. This is the §5.4
    # invariant "healthy slow generation never double-claimed", relocated to the
    # queue: were the claim to precede the permit, the queued row would commit
    # `generating`, could go stale mid-queue (wait > GENERATION_STALE_AFTER), get
    # re-claimed, and run the model twice. Two distinct paths so the two
    # generations race concurrently (a single path's chain is serial regardless).
    entered = asyncio.Event()
    release = asyncio.Event()  # holds the permit-holder inside the model

    def resolver() -> Callable[[str], Model]:
        async def respond(
            messages: list[ModelMessage], info: AgentInfo
        ) -> ModelResponse:
            text = stub_user_text(messages)
            lesson_tool = stub_tool_with(info.output_tools, "read_passage")
            if lesson_tool is not None:
                entered.set()
                await release.wait()
                return _lesson_reply(text, lesson_tool.name)
            outline_tool = stub_tool_with(info.output_tools, "units")
            assert outline_tool is not None
            return _outline_reply(text, outline_tool.name)

        model = FunctionModel(respond)
        return lambda _id: model

    gated = _GatedSlot(1)
    orch = _build_orchestrator(
        resolve_model_fn=resolver(),
        spawn=asyncio.create_task,
        stale_after_seconds=180,
        model_slot=gated,
    )

    path_ids = await _seed_ready_paths_one_lesson(2)
    runners = [
        asyncio.create_task(orch.ensure_generated_through(path_id, 1))
        for path_id in path_ids
    ]

    # The permit-holder is inside the model (so it has claimed + committed
    # `generating`); the other caller has reached the permit gate.
    await asyncio.wait_for(entered.wait(), timeout=2.0)
    await asyncio.wait_for(gated.second_reached.wait(), timeout=2.0)

    rows = [(await _generated_lessons(path_id))[0] for path_id in path_ids]
    generating = [
        row for row in rows if row.generation_state is LessonGenerationState.GENERATING
    ]
    ungenerated = [
        row for row in rows if row.generation_state is LessonGenerationState.UNGENERATED
    ]
    # Exactly one claimed (the permit-holder); the queued one is NOT claimed.
    assert len(generating) == 1
    assert len(ungenerated) == 1
    assert ungenerated[0].generation_started_at is None

    release.set()
    await asyncio.gather(*runners)
    for path_id in path_ids:
        lessons = await _generated_lessons(path_id)
        assert all(
            le.generation_state is LessonGenerationState.GENERATED for le in lessons
        )


# --------------------------------------------------------------------------- #
# Criterion 3: shutdown with in-flight work leaves rows re-claimable
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_shutdown_cancels_in_flight_and_leaves_row_reclaimable() -> None:
    started = asyncio.Event()
    release = asyncio.Event()  # never set: the model blocks until cancellation

    def resolver() -> Callable[[str], Model]:
        async def respond(
            messages: list[ModelMessage], info: AgentInfo
        ) -> ModelResponse:
            text = stub_user_text(messages)
            lesson_tool = stub_tool_with(info.output_tools, "read_passage")
            if lesson_tool is not None:
                started.set()
                await release.wait()  # wedge here until the task is cancelled
                return _lesson_reply(text, lesson_tool.name)
            outline_tool = stub_tool_with(info.output_tools, "units")
            assert outline_tool is not None
            return _outline_reply(text, outline_tool.name)

        model = FunctionModel(respond)
        return lambda _id: model

    orch = _build_orchestrator(
        resolve_model_fn=resolver(),
        spawn=asyncio.create_task,  # replaced by bind_runtime in start()
        stale_after_seconds=180,
        prefetch_n=1,
    )
    lifecycle = GenerationLifecycle(orch, session_factory=_session_factory)
    await lifecycle.start()

    path_id, ids = await _seed_path_with_lessons(
        [(1, LessonGenerationState.UNGENERATED)]
    )
    # Kick generation through the bound registry spawn; the model wedges, so the
    # lesson row commits `generating` and stays there.
    lifecycle.registry.spawn(orch.ensure_generated_through(path_id, 1))
    await asyncio.wait_for(started.wait(), timeout=2.0)

    async with db.async_session() as session:
        from aleph.models import Lesson

        wedged = await session.get(Lesson, ids[1])
        assert wedged is not None
        assert wedged.generation_state is LessonGenerationState.GENERATING

    # Graceful shutdown mid-flight.
    await lifecycle.stop()

    # The in-flight task was actually cancelled and drained (not left to leak).
    assert len(lifecycle.registry) == 0

    # The row was NOT marked failed and NO cleanup ran — CancelledError
    # propagated (§5.4). It stays `generating` and is re-claimable via stale
    # recovery: a claim under a zero stale window wins.
    assert await _lesson_state(ids[1]) is LessonGenerationState.GENERATING
    async with db.async_session() as session:
        fence = await LessonRepository(
            session, stale_after_seconds=0
        ).claim_for_generation(ids[1])
        await session.commit()
    assert fence is not None  # the wedged row is cleanly re-claimable


# --------------------------------------------------------------------------- #
# Criterion 4: a task raising an unexpected exception records `failed`
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_unexpected_exception_in_task_records_failed_and_drains_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An UNEXPECTED error in the task body *after* the model call — outside the
    # agent's own try/except — must be caught by the task's TOP-LEVEL handler and
    # recorded as `failed`, never wedged in `generating`, never leaked out of the
    # spawned task (§5.4). Here the quick-check persist raises. Driven through the
    # real TaskRegistry so the strong-ref + no-leak path is exercised: if the task
    # leaked, an unretrieved-exception would surface and the row would stay
    # `generating`.
    from aleph.repositories import QuickCheckRepository

    async def _boom(self: object, **_kwargs: object) -> None:
        raise RuntimeError("forced persist failure in task body")

    monkeypatch.setattr(QuickCheckRepository, "create", _boom)

    registry = TaskRegistry()

    def resolver() -> Callable[[str], Model]:
        async def respond(
            messages: list[ModelMessage], info: AgentInfo
        ) -> ModelResponse:
            text = stub_user_text(messages)
            lesson_tool = stub_tool_with(info.output_tools, "read_passage")
            if lesson_tool is not None:
                return _lesson_reply(text, lesson_tool.name)
            outline_tool = stub_tool_with(info.output_tools, "units")
            assert outline_tool is not None
            return _outline_reply(text, outline_tool.name)

        model = FunctionModel(respond)
        return lambda _id: model

    orch = _build_orchestrator(
        resolve_model_fn=resolver(),
        spawn=registry.spawn,
        stale_after_seconds=180,
        prefetch_n=1,
    )

    path_id, ids = await _seed_path_with_lessons(
        [(1, LessonGenerationState.UNGENERATED)]
    )

    registry.spawn(orch.ensure_generated_through(path_id, 1))
    await registry.join()  # must not raise: the handler swallows and records failed

    assert await _lesson_state(ids[1]) is LessonGenerationState.FAILED
    async with db.async_session() as session:
        lesson = await session.get(Lesson, ids[1])
        assert lesson is not None
        assert lesson.generation_error  # a generic message was recorded
    assert len(registry) == 0  # done-callback discarded the finished task
