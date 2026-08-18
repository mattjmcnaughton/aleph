"""Integration tests for the generation orchestrator (AL-040, TDD §5.4/§5.5/§5.2).

Against real Postgres with the deterministic stub model (or a controllable
``FunctionModel``) driving generation — the load-bearing orchestration behaviour
the ticket's acceptance criteria pin down:

* **Ordering invariant** — under interleaved polls (concurrent ``ensure`` calls),
  lessons are still generated serially in ``position_in_path`` order, exactly
  once (§5.4).
* **A failed lesson stops the chain**; an explicit retry resumes it (§5.4/§5.5).
* **Refusal** → ``refused`` + message, no units created (§5.5, W7).
* **Timeout** → ``failed``, never stuck ``generating`` past the stale window
  (§5.5, W8).
* **Poll on a stale row** re-claims and completes it (§5.4 poll-as-trigger).

Claim/stale/timeout behaviour is real (DB clock, asyncio), so these live in
integration against real Postgres (fakes over mocks): the stub model is the one
fake, injected at the model-resolution seam exactly as production resolves it.
"""

from __future__ import annotations

import asyncio
import itertools
import uuid as uuid_runtime
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import func, select

from aleph import db
from aleph.config import Settings
from aleph.domains.answer_order import shuffle_options
from aleph.models import (
    Lesson,
    LessonGenerationState,
    Level,
    Path,
    PathStatus,
    QuickCheck,
    Unit,
)
from aleph.services.generation import GenerationOrchestrator
from aleph.services.stub_model import (
    StubModelForcedError,
    force_lesson_failure,
)
from aleph.services.stub_model import (
    _build_lesson as stub_build_lesson,
)
from aleph.services.stub_model import (
    _build_outline as stub_build_outline,
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
from aleph.services.stub_model import (
    clean_topic as stub_clean_topic,
)

from .conftest import CollectingSpawn, create_user, stub_resolver

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable
    from typing import Any

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model
    from pydantic_ai.models.function import AgentInfo

    from aleph.agents.lesson import QuickCheck as QuickCheckPayload


# --------------------------------------------------------------------------- #
# Test doubles: a controllable model at the resolver seam
#
# ``CollectingSpawn`` and ``stub_resolver`` live in ``conftest`` (shared with the
# ``test_paths_api`` HTTP surface); the controllable/capturing resolvers below are
# local to this module.
# --------------------------------------------------------------------------- #


@dataclass
class ModelControl:
    """Toggles for the controllable model (independent of topic sentinels)."""

    fail_lessons: set[int] = field(default_factory=set)
    fail_outline: bool = False
    refuse: bool = False
    lesson_delay: float = 0.0


def controllable_resolver(control: ModelControl) -> Callable[[str], Model]:
    """A ``resolve_model_fn`` returning a model driven by ``control``.

    Reuses the stub's content builders (schema-valid, deterministic) but decides
    failure/refusal/delay from ``control`` rather than the topic string — so a
    test can make a retry *succeed* on the very topic whose sentinel first failed.
    """

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        text = stub_user_text(messages)
        topic = stub_clean_topic(text) or "the topic"
        lesson_tool = stub_tool_with(info.output_tools, "read_passage")
        if lesson_tool is not None:
            position = stub_read_position(text)
            if control.lesson_delay:
                await asyncio.sleep(control.lesson_delay)
            if position in control.fail_lessons:
                raise StubModelForcedError(f"forced lesson failure at {position}")
            content = stub_build_lesson(topic, position or 1)
            return ModelResponse(
                parts=[
                    ToolCallPart(tool_name=lesson_tool.name, args=content.model_dump())
                ]
            )
        outline_tool = stub_tool_with(info.output_tools, "units")
        refusal_tool = stub_tool_with(info.output_tools, "message")
        if control.fail_outline:
            raise StubModelForcedError("forced outline failure")
        if control.refuse and refusal_tool is not None:
            from aleph.agents.outline import Refusal

            refusal = Refusal(message="This topic is outside what the tutor teaches.")
            return ModelResponse(
                parts=[
                    ToolCallPart(tool_name=refusal_tool.name, args=refusal.model_dump())
                ]
            )
        assert outline_tool is not None
        outline = stub_build_outline(topic)
        return ModelResponse(
            parts=[ToolCallPart(tool_name=outline_tool.name, args=outline.model_dump())]
        )

    model = FunctionModel(respond)
    return lambda _model_id: model


def capturing_outline_resolver(captured: list[str]) -> Callable[[str], Model]:
    """A resolver whose model records the outline call's *user prompt* verbatim.

    Sibling to :func:`capturing_resolver` (which captures the lesson prompt);
    this one captures the outline's, so a test can assert exactly what
    :func:`~aleph.agents.outline.build_outline_prompt` produced actually
    reached the model call — not just that ``paths.guidance`` persisted.
    """

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        text = stub_user_text(messages)
        outline_tool = stub_tool_with(info.output_tools, "units")
        if outline_tool is not None:
            captured.append(text)
            topic = stub_clean_topic(text) or "the topic"
            outline = stub_build_outline(topic)
            return ModelResponse(
                parts=[
                    ToolCallPart(tool_name=outline_tool.name, args=outline.model_dump())
                ]
            )
        lesson_tool = stub_tool_with(info.output_tools, "read_passage")
        assert lesson_tool is not None
        topic = stub_clean_topic(text) or "the topic"
        content = stub_build_lesson(topic, stub_read_position(text) or 1)
        return ModelResponse(
            parts=[ToolCallPart(tool_name=lesson_tool.name, args=content.model_dump())]
        )

    model = FunctionModel(respond)
    return lambda _model_id: model


def quick_check_capturing_resolver(
    posed: dict[int, QuickCheckPayload],
) -> Callable[[str], Model]:
    """A resolver whose model records the Quick check it posed, by position.

    A test cannot rebuild what the stub posed from the topic alone: the stub
    seeds its content on ``clean_topic(prompt)``, which is the *whole* collapsed
    user prompt, not the topic string the path was created with. So the only
    honest source for "what did the model key?" is the call itself — which is
    exactly what the option-order assertion needs, since its claim is "the
    persisted row is what the model posed, re-ordered".
    """

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        text = stub_user_text(messages)
        topic = stub_clean_topic(text) or "the topic"
        lesson_tool = stub_tool_with(info.output_tools, "read_passage")
        if lesson_tool is not None:
            position = stub_read_position(text) or 1
            content = stub_build_lesson(topic, position)
            posed[position] = content.quick_check
            return ModelResponse(
                parts=[
                    ToolCallPart(tool_name=lesson_tool.name, args=content.model_dump())
                ]
            )
        outline_tool = stub_tool_with(info.output_tools, "units")
        assert outline_tool is not None
        outline = stub_build_outline(topic)
        return ModelResponse(
            parts=[ToolCallPart(tool_name=outline_tool.name, args=outline.model_dump())]
        )

    model = FunctionModel(respond)
    return lambda _model_id: model


def capturing_resolver(captured: list[str]) -> Callable[[str], Model]:
    """A resolver whose model records every *user prompt* it is asked to run.

    Lets a test assert what the continuity prompt actually carries (the prior
    passages and their unit/lesson locators, §5.2) rather than only the DB result.
    """

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        text = stub_user_text(messages)
        topic = stub_clean_topic(text) or "the topic"
        lesson_tool = stub_tool_with(info.output_tools, "read_passage")
        if lesson_tool is not None:
            captured.append(text)
            content = stub_build_lesson(topic, stub_read_position(text) or 1)
            return ModelResponse(
                parts=[
                    ToolCallPart(tool_name=lesson_tool.name, args=content.model_dump())
                ]
            )
        outline_tool = stub_tool_with(info.output_tools, "units")
        assert outline_tool is not None
        outline = stub_build_outline(topic)
        return ModelResponse(
            parts=[ToolCallPart(tool_name=outline_tool.name, args=outline.model_dump())]
        )

    model = FunctionModel(respond)
    return lambda _model_id: model


def make_orchestrator(
    *,
    resolve_model_fn: Callable[[str], Model],
    spawn: CollectingSpawn | None = None,
    generation_timeout_seconds: float = 30.0,
    outline_timeout_seconds: float = 30.0,
    prefetch_n: int = 2,
    config: Settings | None = None,
) -> tuple[GenerationOrchestrator, CollectingSpawn]:
    # ``build_prior_context`` (D7) reads caps like ``continuity_passages_max``
    # straight off ``self._config`` rather than through a dedicated constructor
    # kwarg (there is no per-cap override here, unlike ``prefetch_n``) — so a
    # test that needs a non-default cap passes a whole ``Settings`` instance.
    spawn = spawn or CollectingSpawn()
    orch = GenerationOrchestrator(
        session_factory=lambda: db.async_session(),
        spawn=spawn,
        resolve_model_fn=resolve_model_fn,
        config=config if config is not None else Settings(),
        generation_timeout_seconds=generation_timeout_seconds,
        # The outline keeps a generous, separate budget so tightening the lesson
        # timeout (to force a lesson timeout) never also governs the outline run
        # — that shared budget was a CI flake risk (advisory 8).
        outline_timeout_seconds=outline_timeout_seconds,
        prefetch_n=prefetch_n,
    )
    return orch, spawn


# --------------------------------------------------------------------------- #
# arrange helpers
# --------------------------------------------------------------------------- #


async def _generated_lessons(path_id: uuid.UUID) -> list[Lesson]:
    async with db.async_session() as session:
        result = await session.execute(
            select(Lesson)
            .where(Lesson.path_id == path_id)
            .order_by(Lesson.position_in_path)
        )
        return list(result.scalars())


async def _count_units(path_id: uuid.UUID) -> int:
    async with db.async_session() as session:
        result = await session.execute(
            select(func.count()).select_from(Unit).where(Unit.path_id == path_id)
        )
        return result.scalar_one()


async def _reload_path(path_id: uuid.UUID) -> Path:
    async with db.async_session() as session:
        path = await session.get(Path, path_id)
        assert path is not None
        return path


async def _seed_path_with_lessons(
    states: list[tuple[int, LessonGenerationState]],
    *,
    completed: tuple[int, ...] = (),
) -> tuple[uuid.UUID, dict[int, uuid.UUID]]:
    """Insert a ready path whose lessons are in the given generation states.

    ``states`` is ``(position_in_path, state)`` pairs; ``completed`` marks which
    positions carry a ``completed_at``. Generated lessons get a Read passage so
    continuity context is loadable. Returns ``(path_id, {position: lesson_id})``.
    """
    async with db.async_session() as session:
        user = await create_user(session)
        path = Path(
            user_id=user.id,
            topic="Seeded topic",
            level=Level.SOME_EXPERIENCE,
            status=PathStatus.READY,
        )
        session.add(path)
        await session.flush()
        unit = Unit(path=path, position=1, title="Unit 1", summary="s")
        session.add(unit)
        await session.flush()
        ids: dict[int, uuid.UUID] = {}
        for position, state in states:
            extra: dict[str, Any] = {}
            if state is LessonGenerationState.GENERATED:
                extra = {
                    "read_passage": f"passage {position}",
                    "generated_at": datetime.now(UTC),
                }
            elif state is LessonGenerationState.FAILED:
                extra = {"generation_error": "seeded failure"}
            lesson = Lesson(
                unit=unit,
                path=path,
                position_in_path=position,
                position_in_unit=position,
                title=f"Lesson {position}",
                generation_state=state,
                completed_at=datetime.now(UTC) if position in completed else None,
                **extra,
            )
            session.add(lesson)
            await session.flush()
            ids[position] = lesson.id
        await session.commit()
        return path.id, ids


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_create_path_generates_outline_and_prefetch_window() -> None:
    orch, spawn = make_orchestrator(resolve_model_fn=stub_resolver(), prefetch_n=2)
    async with db.async_session() as session:
        user = await create_user(session)
        await session.commit()
        user_id = user.id

    path = await orch.create_path(
        user_id=user_id, topic="Rust ownership", level=Level.SOME_EXPERIENCE
    )
    path_id = path.id
    assert path.status is PathStatus.PENDING  # 202 is returned before generation

    await spawn.drain()

    reloaded = await _reload_path(path_id)
    assert reloaded.status is PathStatus.READY
    assert await _count_units(path_id) >= 1

    lessons = await _generated_lessons(path_id)
    assert len(lessons) >= 1
    # Prefetch window = first_incomplete(1) + PREFETCH_N(2) = 3 lessons generated.
    generated = [
        lesson
        for lesson in lessons
        if lesson.generation_state is LessonGenerationState.GENERATED
    ]
    assert len(generated) == min(3, len(lessons))
    # Generated lessons are a contiguous prefix (ordering invariant).
    assert [lesson.position_in_path for lesson in generated] == list(
        range(1, len(generated) + 1)
    )


# --------------------------------------------------------------------------- #
# Quick check option order (``domains/answer_order.py``)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_persisted_quick_check_options_are_reordered_not_taken_as_keyed() -> None:
    """The row's option order is the app's, not the model's — same keyed answer.

    The model keys the correct option to the same slot far more often than
    chance ("always B"), so the served order is decided at persist by
    :func:`~aleph.domains.answer_order.shuffle_options`, seeded on the lesson id.
    Asserted against what the model *actually posed* (captured at the call, since
    the stub seeds on the whole prompt rather than the topic): the persisted
    options are exactly those options re-ordered by that function, and
    ``correct_index`` still addresses the same option **text** the model keyed.
    """
    posed: dict[int, QuickCheckPayload] = {}
    orch, spawn = make_orchestrator(
        resolve_model_fn=quick_check_capturing_resolver(posed), prefetch_n=2
    )
    async with db.async_session() as session:
        user = await create_user(session)
        await session.commit()
        user_id = user.id

    path = await orch.create_path(
        user_id=user_id, topic="Rust ownership", level=Level.SOME_EXPERIENCE
    )
    await spawn.drain()

    generated = [
        lesson
        for lesson in await _generated_lessons(path.id)
        if lesson.generation_state is LessonGenerationState.GENERATED
    ]
    assert generated, "no lesson was generated"

    for lesson in generated:
        check = posed[lesson.position_in_path]
        async with db.async_session() as session:
            row = (
                await session.execute(
                    select(QuickCheck).where(QuickCheck.lesson_id == lesson.id)
                )
            ).scalar_one()

        expected = shuffle_options(
            check.options, check.correct_index, seed=str(lesson.id)
        )
        assert row.options == list(expected.options)
        assert row.correct_index == expected.correct_index
        # Nothing about the check itself changed — only where the answer sits.
        assert sorted(row.options) == sorted(check.options)
        assert row.options[row.correct_index] == check.options[check.correct_index]


# --------------------------------------------------------------------------- #
# Guidance (CONTEXT.md): persisted on the row, reaches the outline prompt
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_create_path_with_guidance_persists_and_reaches_outline_prompt() -> None:
    captured: list[str] = []
    orch, spawn = make_orchestrator(
        resolve_model_fn=capturing_outline_resolver(captured)
    )
    async with db.async_session() as session:
        user = await create_user(session)
        await session.commit()
        user_id = user.id

    guidance = "Focus on hands-on examples; skip the theoretical history."
    path = await orch.create_path(
        user_id=user_id,
        topic="Rust ownership",
        level=Level.SOME_EXPERIENCE,
        guidance=guidance,
    )
    await spawn.drain()

    # Persisted on the row (not just carried into the one spawned task) — the
    # same DB-driven-resume rationale as the admin model overrides (§5.4/D6).
    reloaded = await _reload_path(path.id)
    assert reloaded.guidance == guidance

    # The outline call's actual user prompt carries the labelled, delimited
    # guidance block build_outline_prompt produces — not just the bare topic.
    assert captured, "the outline call should have run and been captured"
    outline_prompt = captured[0]
    assert "Topic: Rust ownership" in outline_prompt
    assert "<guidance>" in outline_prompt
    assert guidance in outline_prompt


@pytest.mark.anyio
async def test_create_path_without_guidance_persists_none() -> None:
    orch, spawn = make_orchestrator(resolve_model_fn=stub_resolver())
    async with db.async_session() as session:
        user = await create_user(session)
        await session.commit()
        user_id = user.id

    path = await orch.create_path(
        user_id=user_id, topic="Rust ownership", level=Level.SOME_EXPERIENCE
    )
    await spawn.drain()

    reloaded = await _reload_path(path.id)
    assert reloaded.guidance is None


# --------------------------------------------------------------------------- #
# refusal → refused + message, no units (W7)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_refusal_marks_refused_with_message_and_no_units() -> None:
    orch, spawn = make_orchestrator(resolve_model_fn=stub_resolver())
    async with db.async_session() as session:
        user = await create_user(session)
        await session.commit()
        user_id = user.id

    path = await orch.create_path(
        user_id=user_id,
        topic="Please help [force-refusal] with this",
        level=Level.NEW_TO_IT,
    )
    await spawn.drain()

    reloaded = await _reload_path(path.id)
    assert reloaded.status is PathStatus.REFUSED
    assert reloaded.refusal_message
    assert await _count_units(path.id) == 0
    assert await _generated_lessons(path.id) == []


# --------------------------------------------------------------------------- #
# outline failure → failed, no units
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_outline_failure_marks_failed_no_units() -> None:
    orch, spawn = make_orchestrator(resolve_model_fn=stub_resolver())
    async with db.async_session() as session:
        user = await create_user(session)
        await session.commit()
        user_id = user.id

    path = await orch.create_path(
        user_id=user_id, topic="[force-outline-failure] topic", level=Level.NEW_TO_IT
    )
    await spawn.drain()

    reloaded = await _reload_path(path.id)
    assert reloaded.status is PathStatus.FAILED
    assert await _count_units(path.id) == 0


# --------------------------------------------------------------------------- #
# ordering invariant under interleaved polls
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_ordering_invariant_under_interleaved_polls() -> None:
    orch, spawn = make_orchestrator(resolve_model_fn=stub_resolver(), prefetch_n=0)
    async with db.async_session() as session:
        user = await create_user(session)
        await session.commit()
        user_id = user.id

    path = await orch.create_path(
        user_id=user_id, topic="Distributed systems", level=Level.WORK_IN_IT
    )
    await spawn.drain()  # outline ready; prefetch_n=0 so ~1 lesson generated
    path_id = path.id

    total = len(await _generated_lessons(path_id))
    assert total >= 6

    # Five interleaved "polls": concurrent idempotent ensure calls through the
    # whole path. Concurrency must not break the serial-in-order, exactly-once
    # invariant — the atomic claim is the only coordination.
    await asyncio.gather(
        *[orch.ensure_generated_through(path_id, 999) for _ in range(5)]
    )

    lessons = await _generated_lessons(path_id)
    # Exactly-once & complete: every lesson generated with exactly one quick check.
    assert all(
        lesson.generation_state is LessonGenerationState.GENERATED for lesson in lessons
    )
    async with db.async_session() as session:
        qc_count = (
            await session.execute(
                select(func.count())
                .select_from(QuickCheck)
                .join(Lesson)
                .where(Lesson.path_id == path_id)
            )
        ).scalar_one()
    assert qc_count == len(lessons)

    # Serial in order: generated_at is non-decreasing by position_in_path.
    stamps = [lesson.generated_at for lesson in lessons]
    assert all(
        a is not None and b is not None and a <= b
        for a, b in itertools.pairwise(stamps)
    )


# --------------------------------------------------------------------------- #
# failed lesson stops the chain; explicit retry resumes
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_failed_lesson_stops_chain_then_retry_resumes() -> None:
    # The real stub honours the topic sentinel and fails lesson at position 2.
    fail_orch, fail_spawn = make_orchestrator(
        resolve_model_fn=stub_resolver(), prefetch_n=0
    )
    async with db.async_session() as session:
        user = await create_user(session)
        await session.commit()
        user_id = user.id

    topic = f"Kubernetes {force_lesson_failure(2)}"
    path = await fail_orch.create_path(
        user_id=user_id, topic=topic, level=Level.SOME_EXPERIENCE
    )
    await fail_spawn.drain()
    path_id = path.id

    # Drive generation through position 5. Lesson 1 generates, lesson 2 fails,
    # the chain STOPS — lessons 3+ stay ungenerated (they need lesson 2's content).
    await fail_orch.ensure_generated_through(path_id, 5)

    lessons = await _generated_lessons(path_id)
    by_pos = {lesson.position_in_path: lesson for lesson in lessons}
    assert by_pos[1].generation_state is LessonGenerationState.GENERATED
    assert by_pos[2].generation_state is LessonGenerationState.FAILED
    assert by_pos[2].generation_error
    assert by_pos[3].generation_state is LessonGenerationState.UNGENERATED

    # Explicit retry with a model that no longer fails lesson 2 (same topic,
    # sentinel still present — proving the retry claim re-runs a *real* failure).
    ok_orch, _ = make_orchestrator(
        resolve_model_fn=controllable_resolver(ModelControl()), prefetch_n=2
    )
    await ok_orch.retry_lesson(by_pos[2].id)

    resumed = await _generated_lessons(path_id)
    resumed_by_pos = {lesson.position_in_path: lesson for lesson in resumed}
    assert resumed_by_pos[2].generation_state is LessonGenerationState.GENERATED
    # The chain resumed past the previously-failed lesson (prefetch window filled).
    assert resumed_by_pos[3].generation_state is LessonGenerationState.GENERATED


# --------------------------------------------------------------------------- #
# timeout → failed, never stuck generating past the stale window
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_lesson_timeout_marks_failed_not_stuck_generating() -> None:
    control = ModelControl(lesson_delay=0.5)  # slower than the timeout below
    orch, spawn = make_orchestrator(
        resolve_model_fn=controllable_resolver(control),
        generation_timeout_seconds=0.1,
        prefetch_n=0,
    )
    async with db.async_session() as session:
        user = await create_user(session)
        await session.commit()
        user_id = user.id

    path = await orch.create_path(
        user_id=user_id, topic="Slow topic", level=Level.NEW_TO_IT
    )
    await (
        spawn.drain()
    )  # outline is fast (no delay); prefetch tries lesson 1 → times out
    path_id = path.id

    lessons = await _generated_lessons(path_id)
    lesson_one = next(lesson for lesson in lessons if lesson.position_in_path == 1)
    assert lesson_one.generation_state is LessonGenerationState.FAILED
    assert lesson_one.generation_state is not LessonGenerationState.GENERATING
    assert lesson_one.generation_error
    assert "tim" in lesson_one.generation_error.lower()  # "timed out"


# --------------------------------------------------------------------------- #
# poll on a stale row re-claims and completes
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_poll_on_stale_generating_row_reclaims_and_completes() -> None:
    orch, _ = make_orchestrator(resolve_model_fn=stub_resolver())
    stale_started = datetime.now(UTC) - timedelta(minutes=10)  # > stale window (3 min)

    async with db.async_session() as session:
        user = await create_user(session)
        path = Path(
            user_id=user.id,
            topic="Graph theory",
            level=Level.SOME_EXPERIENCE,
            status=PathStatus.READY,
        )
        session.add(path)
        await session.flush()
        unit = Unit(path=path, position=1, title="Unit 1", summary="s")
        session.add(unit)
        await session.flush()
        # Lesson 1 is wedged in `generating` from a crashed run (backdated stamp).
        wedged = Lesson(
            unit=unit,
            path=path,
            position_in_path=1,
            position_in_unit=1,
            title="Lesson 1",
            generation_state=LessonGenerationState.GENERATING,
            generation_started_at=stale_started,
        )
        session.add(wedged)
        for position in (2, 3):
            session.add(
                Lesson(
                    unit=unit,
                    path=path,
                    position_in_path=position,
                    position_in_unit=position,
                    title=f"Lesson {position}",
                )
            )
        await session.commit()
        path_id = path.id
        wedged_id = wedged.id

    # A poll runs the same idempotent ensure: the stale row is re-claimed and completed.
    await orch.ensure_generated_through(path_id, 1)

    async with db.async_session() as session:
        reclaimed = await session.get(Lesson, wedged_id)
        assert reclaimed is not None
        assert reclaimed.generation_state is LessonGenerationState.GENERATED
        assert reclaimed.read_passage


# --------------------------------------------------------------------------- #
# BLOCKING 1: retry must not generate a lesson out of order (ordering invariant)
# --------------------------------------------------------------------------- #


async def _lesson_state(lesson_id: uuid.UUID) -> LessonGenerationState:
    async with db.async_session() as session:
        lesson = await session.get(Lesson, lesson_id)
        assert lesson is not None
        return lesson.generation_state


@pytest.mark.anyio
async def test_retry_lesson_not_at_chain_head_does_not_generate_out_of_order() -> None:
    # Lesson 1 generated, 2-4 ungenerated, lesson 5 failed. Retrying lesson 5
    # while its predecessors are ungenerated MUST NOT generate it — its prior
    # context would be incomplete, and `generated` is terminal/immutable, so the
    # ordering invariant would be permanently violated (PRD §5.2).
    orch, _ = make_orchestrator(
        resolve_model_fn=controllable_resolver(ModelControl()), prefetch_n=0
    )
    path_id, ids = await _seed_path_with_lessons(
        [
            (1, LessonGenerationState.GENERATED),
            (2, LessonGenerationState.UNGENERATED),
            (3, LessonGenerationState.UNGENERATED),
            (4, LessonGenerationState.UNGENERATED),
            (5, LessonGenerationState.FAILED),
        ]
    )

    await orch.retry_lesson(ids[5])

    assert await _lesson_state(ids[5]) is not LessonGenerationState.GENERATED
    assert await _lesson_state(ids[5]) is LessonGenerationState.FAILED


@pytest.mark.anyio
async def test_retry_failed_lesson_at_chain_head_resumes_the_chain() -> None:
    # Lessons 1-2 generated, lesson 3 failed, 4-5 ungenerated. Retrying lesson 3
    # (all predecessors generated → it IS the chain head) regenerates it and the
    # chain resumes onward to lesson 4.
    orch, _ = make_orchestrator(
        resolve_model_fn=controllable_resolver(ModelControl()), prefetch_n=3
    )
    path_id, ids = await _seed_path_with_lessons(
        [
            (1, LessonGenerationState.GENERATED),
            (2, LessonGenerationState.GENERATED),
            (3, LessonGenerationState.FAILED),
            (4, LessonGenerationState.UNGENERATED),
            (5, LessonGenerationState.UNGENERATED),
        ]
    )

    await orch.retry_lesson(ids[3])

    assert await _lesson_state(ids[3]) is LessonGenerationState.GENERATED
    # first_incomplete=1, prefetch_n=3 → window through position 4: chain resumes.
    assert await _lesson_state(ids[4]) is LessonGenerationState.GENERATED


# --------------------------------------------------------------------------- #
# BLOCKING 2: a persist/context failure in the task body records `failed`
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_lesson_persist_failure_records_failed_not_stuck_generating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A failure AFTER the claim, outside the agent's own try/except (here the
    # quick-check persist), must not leave the row wedged in `generating` until
    # the stale window, nor leak an unretrieved exception out of the spawned
    # task (§5.4: every task body has a top-level handler that records `failed`).
    from aleph.repositories import QuickCheckRepository

    async def _boom(self: object, **_kwargs: object) -> None:
        raise RuntimeError("forced persist failure")

    monkeypatch.setattr(QuickCheckRepository, "create", _boom)

    orch, spawn = make_orchestrator(
        resolve_model_fn=controllable_resolver(ModelControl()), prefetch_n=1
    )
    async with db.async_session() as session:
        user = await create_user(session)
        await session.commit()
        user_id = user.id

    path = await orch.create_path(
        user_id=user_id, topic="Persist boom", level=Level.NEW_TO_IT
    )
    await spawn.drain()  # must not raise: the handler swallows and records failed

    lessons = await _generated_lessons(path.id)
    lesson_one = next(le for le in lessons if le.position_in_path == 1)
    assert lesson_one.generation_state is LessonGenerationState.FAILED
    assert lesson_one.generation_error  # generic message recorded


# --------------------------------------------------------------------------- #
# poll targets & snapshot (advisory 7)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_poll_path_returns_snapshot_and_triggers_resume() -> None:
    orch, spawn = make_orchestrator(resolve_model_fn=stub_resolver(), prefetch_n=2)
    async with db.async_session() as session:
        user = await create_user(session)
        await session.commit()
        user_id = user.id

    path = await orch.create_path(
        user_id=user_id, topic="Poll topic", level=Level.SOME_EXPERIENCE
    )
    await spawn.drain()

    snapshot = await orch.poll_path(path.id)
    await spawn.drain()  # poll spawns a resume
    assert snapshot is not None
    assert snapshot.status is PathStatus.READY
    assert snapshot.refusal_message is None
    assert snapshot.progress.total_lessons >= 1
    assert snapshot.progress.generated_lessons >= 1

    assert await orch.poll_path(uuid_runtime.uuid4()) is None


@pytest.mark.anyio
async def test_poll_path_snapshot_carries_refusal_message() -> None:
    orch, spawn = make_orchestrator(resolve_model_fn=stub_resolver())
    async with db.async_session() as session:
        user = await create_user(session)
        await session.commit()
        user_id = user.id

    path = await orch.create_path(
        user_id=user_id,
        topic="Please help [force-refusal] here",
        level=Level.NEW_TO_IT,
    )
    await spawn.drain()

    snapshot = await orch.poll_path(path.id)
    await spawn.drain()
    assert snapshot is not None
    assert snapshot.status is PathStatus.REFUSED
    assert snapshot.refusal_message


@pytest.mark.anyio
async def test_poll_lesson_reports_effective_state_and_reclaims_stale() -> None:
    orch, spawn = make_orchestrator(resolve_model_fn=stub_resolver())
    stale_started = datetime.now(UTC) - timedelta(minutes=10)
    path_id, ids = await _seed_path_with_lessons(
        [(1, LessonGenerationState.GENERATED), (2, LessonGenerationState.UNGENERATED)],
    )
    # Wedge lesson 2 in a stale `generating` (a crashed run).
    async with db.async_session() as session:
        lesson = await session.get(Lesson, ids[2])
        assert lesson is not None
        lesson.generation_state = LessonGenerationState.GENERATING
        lesson.generation_started_at = stale_started
        await session.commit()

    # The real poll method: a stale `generating` reads as `failed` (effective).
    state = await orch.poll_lesson(ids[2])
    assert state is LessonGenerationState.FAILED
    await spawn.drain()  # the poll spawned a resume that re-claims + completes it
    assert await _lesson_state(ids[2]) is LessonGenerationState.GENERATED

    assert await orch.poll_lesson(uuid_runtime.uuid4()) is None


# --------------------------------------------------------------------------- #
# continuity: lesson N's prompt carries N-1 prior passages with real unit titles
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_lesson_prompt_carries_prior_passages_with_real_unit_titles() -> None:
    # Lessons 1-2 generated under "Unit 1"; generating lesson 3 must feed the
    # model the two prior passages, each prefixed with its REAL unit title (not
    # the lesson title twice — advisory 4 / §5.2 "prefixed by unit/lesson title").
    captured: list[str] = []
    orch, _ = make_orchestrator(
        resolve_model_fn=capturing_resolver(captured), prefetch_n=0
    )
    path_id, _ids = await _seed_path_with_lessons(
        [
            (1, LessonGenerationState.GENERATED),
            (2, LessonGenerationState.GENERATED),
            (3, LessonGenerationState.UNGENERATED),
        ]
    )

    await orch.ensure_generated_through(path_id, 3)

    lesson_three_prompts = [p for p in captured if "position_in_path=3" in p]
    assert lesson_three_prompts, "lesson 3 was never generated"
    prompt = lesson_three_prompts[0]
    # Real unit title reaches the model, paired with each prior lesson's title.
    assert "[Unit 1 / Lesson 1]" in prompt
    assert "[Unit 1 / Lesson 2]" in prompt
    # And the prior passages themselves travel (continuity payload).
    assert "passage 1" in prompt
    assert "passage 2" in prompt


@pytest.mark.anyio
async def test_prior_context_windows_to_the_most_recent_passages() -> None:
    # F2/D7: a path longer than ``continuity_passages_max`` must not carry every
    # prior passage verbatim — only the most recent window, oldest-to-newest.
    # Window = 2 over 3 generated priors (lessons 1-3) generating lesson 4: only
    # lessons 2-3 (the two most recent) should appear, lesson 1 should not.
    orch, _ = make_orchestrator(
        resolve_model_fn=capturing_resolver([]),
        config=Settings(continuity_passages_max=2),
    )
    path_id, _ids = await _seed_path_with_lessons(
        [
            (1, LessonGenerationState.GENERATED),
            (2, LessonGenerationState.GENERATED),
            (3, LessonGenerationState.GENERATED),
            (4, LessonGenerationState.UNGENERATED),
        ]
    )

    async with db.async_session() as session:
        prior = await orch.build_prior_context(session, path_id, position_in_path=4)

    assert [p.lesson_title for p in prior] == ["Lesson 2", "Lesson 3"]
    assert [p.read_passage for p in prior] == ["passage 2", "passage 3"]


@pytest.mark.anyio
async def test_prior_context_unchanged_when_shorter_than_the_window() -> None:
    # A path with fewer generated priors than the window is untouched — the
    # window only ever truncates, never pads.
    orch, _ = make_orchestrator(
        resolve_model_fn=capturing_resolver([]),
        config=Settings(continuity_passages_max=30),
    )
    path_id, _ids = await _seed_path_with_lessons(
        [
            (1, LessonGenerationState.GENERATED),
            (2, LessonGenerationState.GENERATED),
            (3, LessonGenerationState.UNGENERATED),
        ]
    )

    async with db.async_session() as session:
        prior = await orch.build_prior_context(session, path_id, position_in_path=3)

    assert [p.lesson_title for p in prior] == ["Lesson 1", "Lesson 2"]
