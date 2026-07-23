"""Unit tests for the assembled lesson agent (ticket AL-032, TDD §5.1/§5.2/§14).

No network and no database: the agent binds no model, so every test injects a
``FunctionModel`` (or the AL-030 deterministic stub, itself a ``FunctionModel``)
at run time and supplies the run inputs through ``LessonDeps``. Mirrors AL-031's
outline test shape (responder with capture + reuse-last, retries-exhausted,
retry-message-reaches-model, deps validation). Three layers are exercised:

- prompt assembly (``build_lesson_prompt``) — prior passages appear verbatim in
  order, titles prefixed, and the ``position_in_path=<N>`` stub contract holds;
- the real assembled agent — a valid lesson passes, each §5.1 violation forces a
  ``ModelRetry``, and the retry budget bounds a persistently-bad model;
- the AL-030 stub driving the real agent (the §12 CI/e2e contract), including the
  ``[force-lesson-failure:N]`` position gate.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from pydantic_ai import UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    ToolCallPart,
)
from pydantic_ai.models.function import FunctionModel

from aleph.agents.lesson import (
    LessonCaps,
    LessonContent,
    LessonDeps,
    PriorPassage,
    build_lesson_agent,
    build_lesson_prompt,
    validate_lesson_content,
)
from aleph.agents.outline import LessonOutline, PathOutline, UnitOutline
from aleph.services.stub_model import (
    StubModelForcedError,
    build_stub_model,
    force_lesson_failure,
)
from tests.unit._lesson_data import content_dict as _valid_content_dict
from tests.unit._lesson_data import passage as _passage

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo
    from pydantic_ai.tools import ToolDefinition


# --- helpers -------------------------------------------------------------------


def _sample_outline() -> PathOutline:
    return PathOutline(
        units=[
            UnitOutline(
                title="Foundations",
                summary="The basics.",
                lessons=[
                    LessonOutline(title="Intro"),
                    LessonOutline(title="Core ideas"),
                ],
            ),
            UnitOutline(
                title="Applications",
                summary="Putting it to work.",
                lessons=[LessonOutline(title="In practice")],
            ),
        ]
    )


def _deps(
    *,
    level: str = "beginner",
    position: int = 1,
    topic: str = "Rust ownership",
    priors: Sequence[PriorPassage] = (),
    caps: LessonCaps | None = None,
) -> LessonDeps:
    # ``level`` is a plain str (some callers loop over it); every caller passes a
    # valid one and ``LessonDeps.__post_init__`` enforces the Level set.
    return LessonDeps(
        topic=topic,
        level=level,  # ty: ignore[invalid-argument-type]
        outline=_sample_outline(),
        position_in_path=position,
        unit_title="Foundations",
        lesson_title="Core ideas",
        prior_passages=tuple(priors),
        caps=caps or LessonCaps(),
    )


def _lesson_tool(output_tools: Sequence[ToolDefinition]) -> ToolDefinition:
    """The lesson agent's single output tool (the one carrying ``read_passage``)."""
    for tool in output_tools:
        if "read_passage" in tool.parameters_json_schema.get("properties", {}):
            return tool
    raise AssertionError("no output tool declares 'read_passage'")


class LessonResponder:
    """FunctionModel callback emitting one lesson-content ``args`` dict per call.

    ``call_count`` lets a test assert a retry happened; ``messages_per_call``
    records what reached the model each call (system prompt, or a fed-back
    ``ModelRetry``). When ``responses`` is exhausted the last entry is reused, so
    a persistently-violating model drives past the retry budget without spelling
    out every identical response (mirrors AL-031's ``OutlineResponder``).
    """

    __name__ = "lesson_responder"

    def __init__(self, responses: list[dict]) -> None:
        self._responses = responses
        self.call_count = 0
        self.messages_per_call: list[list[ModelMessage]] = []

    def __call__(
        self, messages: Sequence[ModelMessage], info: AgentInfo
    ) -> ModelResponse:
        self.messages_per_call.append(list(messages))
        args = self._responses[min(self.call_count, len(self._responses) - 1)]
        self.call_count += 1
        tool = _lesson_tool(info.output_tools)
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=args)])


def _system_prompt_text(messages: Sequence[ModelMessage]) -> str:
    parts: list[str] = []
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, SystemPromptPart) and isinstance(part.content, str):
                parts.append(part.content)
    return "\n".join(parts)


def _retry_prompt_text(messages: Sequence[ModelMessage]) -> str:
    parts: list[str] = []
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, RetryPromptPart):
                content = part.content
                parts.append(content if isinstance(content, str) else str(content))
    return "\n".join(parts)


# --- prompt assembly (build_lesson_prompt) -------------------------------------


def test_prompt_carries_prior_passages_in_order_with_titles() -> None:
    priors = [
        PriorPassage(
            unit_title="Foundations",
            lesson_title="Intro",
            read_passage="First prior passage body verbatim.",
        ),
        PriorPassage(
            unit_title="Foundations",
            lesson_title="Core ideas",
            read_passage="Second prior passage body verbatim.",
        ),
    ]
    prompt = build_lesson_prompt(_deps(position=3, priors=priors))
    i1 = prompt.index("First prior passage body verbatim.")
    i2 = prompt.index("Second prior passage body verbatim.")
    # Passages appear in path order.
    assert i1 < i2
    # Each passage is prefixed by its OWN unit/lesson title, immediately above the
    # body (assert the literal block — ``prompt.index("Intro")`` alone is vacuous,
    # since the outline serialization also names the "Intro" lesson).
    assert "[Foundations / Intro]\nFirst prior passage body verbatim." in prompt
    assert "[Foundations / Core ideas]\nSecond prior passage body verbatim." in prompt


def test_prompt_carries_position_exactly_once_and_before_outline() -> None:
    # The stub reads the FIRST ``position_in_path=<N>`` in the user text as
    # authoritative (services/stub_model.py contract); it must be unique and must
    # precede any outline serialization. We never serialize per-lesson positions,
    # so the token appears exactly once, at the authoritative spot.
    prompt = build_lesson_prompt(_deps(position=7))
    assert prompt.count("position_in_path=7") == 1
    assert len(re.findall(r"position_in_path", prompt)) == 1
    # ...and it precedes the outline serialization (c-4): the stub's first-match
    # read must land on the authoritative token, not any later text.
    assert prompt.index("position_in_path=7") < prompt.index("Full path outline")


def test_prompt_carries_topic_and_this_lessons_titles() -> None:
    prompt = build_lesson_prompt(_deps(topic="Rust ownership", position=2))
    assert "Rust ownership" in prompt
    # The current lesson's unit + title (TDD §5.1 input).
    assert "Core ideas" in prompt


def test_prompt_first_lesson_has_no_prior_passages() -> None:
    # Position 1 has no priors; assembly must still be well-formed and carry the
    # position token exactly once.
    prompt = build_lesson_prompt(_deps(position=1, priors=[]))
    assert prompt.count("position_in_path=1") == 1


# --- deps / caps construction (runtime validation, red-first) ------------------


def test_deps_rejects_unknown_level() -> None:
    # ``Level`` is a typing Literal (not runtime-enforced); __post_init__ rejects
    # a bad value at construction rather than as a bare KeyError in the prompt.
    with pytest.raises(ValueError, match="wizard"):
        LessonDeps(
            topic="t",
            level="wizard",  # ty: ignore[invalid-argument-type]
            outline=_sample_outline(),
            position_in_path=1,
            unit_title="U",
            lesson_title="L",
            prior_passages=(),
        )


def test_deps_accepts_each_valid_level() -> None:
    for level in ("beginner", "intermediate", "advanced"):
        assert _deps(level=level).level == level


def test_deps_rejects_non_positive_position() -> None:
    # position_in_path is the total-order index (1-based); 0 or negative is
    # incoherent and would break the stub's [force-lesson-failure:N] contract.
    with pytest.raises(ValueError, match="position"):
        _deps(position=0)


# --- assembled agent: happy path + system prompt -------------------------------


def test_agent_returns_valid_lesson() -> None:
    agent = build_lesson_agent()
    respond = LessonResponder([_valid_content_dict()])
    deps = _deps()
    result = agent.run_sync(
        build_lesson_prompt(deps), deps=deps, model=FunctionModel(respond)
    ).output
    assert isinstance(result, LessonContent)
    assert respond.call_count == 1


def test_agent_system_prompt_is_level_scoped() -> None:
    agent = build_lesson_agent()
    respond = LessonResponder([_valid_content_dict()])
    deps = _deps(level="advanced")
    agent.run_sync(build_lesson_prompt(deps), deps=deps, model=FunctionModel(respond))
    prompt = _system_prompt_text(respond.messages_per_call[0])
    assert "advanced" in prompt.lower()


def test_agent_system_prompt_carries_caps() -> None:
    # The word/option bands the validator enforces come from ``ctx.deps.caps``, so
    # the system prompt MUST target those same numbers — a static prompt hardcoding
    # the defaults would aim the model at one band while the validator enforces
    # another (guaranteed retry burn under non-default caps). Custom, non-default
    # bands must reach the model.
    agent = build_lesson_agent()
    respond = LessonResponder([_valid_content_dict()])
    caps = LessonCaps(
        option_count_min=2,
        option_count_max=5,
        passage_words_min=150,
        passage_words_max=450,
    )
    deps = _deps(caps=caps)
    agent.run_sync(build_lesson_prompt(deps), deps=deps, model=FunctionModel(respond))
    prompt = _system_prompt_text(respond.messages_per_call[0])
    assert str(caps.passage_words_min) in prompt
    assert str(caps.passage_words_max) in prompt
    assert str(caps.option_count_min) in prompt
    assert str(caps.option_count_max) in prompt


def test_agent_system_prompt_differs_by_level() -> None:
    agent = build_lesson_agent()
    prompts: dict[str, str] = {}
    for level in ("beginner", "intermediate", "advanced"):
        respond = LessonResponder([_valid_content_dict()])
        deps = _deps(level=level)
        agent.run_sync(
            build_lesson_prompt(deps), deps=deps, model=FunctionModel(respond)
        )
        prompts[level] = _system_prompt_text(respond.messages_per_call[0])
    assert len({prompts["beginner"], prompts["intermediate"], prompts["advanced"]}) == 3


# --- assembled agent: ModelRetry on each violation -----------------------------


@pytest.mark.parametrize(
    "invalid",
    [
        pytest.param(
            _valid_content_dict(options=["a", "b"], correct_index=0),
            id="options-too-few",
        ),
        pytest.param(
            _valid_content_dict(options=["a", "b", "c", "d", "e"], correct_index=0),
            id="options-too-many",
        ),
        pytest.param(
            _valid_content_dict(correct_index=9), id="correct-index-out-of-range"
        ),
        pytest.param(
            _valid_content_dict(options=["Same", " same ", "Other"], correct_index=0),
            id="duplicate-options",
        ),
        pytest.param(
            _valid_content_dict(read_passage=_passage(10)), id="passage-too-short"
        ),
        pytest.param(
            _valid_content_dict(read_passage=_passage(600)), id="passage-too-long"
        ),
        pytest.param(_valid_content_dict(stem="   "), id="empty-stem"),
        pytest.param(_valid_content_dict(explanation="  "), id="empty-explanation"),
    ],
)
def test_agent_retries_on_each_violation_then_succeeds(
    invalid: dict[str, object],
) -> None:
    agent = build_lesson_agent()
    deps = _deps()
    respond = LessonResponder([invalid, _valid_content_dict()])
    result = agent.run_sync(
        build_lesson_prompt(deps), deps=deps, model=FunctionModel(respond)
    ).output
    assert isinstance(result, LessonContent)
    assert respond.call_count == 2  # the ModelRetry forced a second call


def test_agent_feeds_validator_message_into_retry() -> None:
    # The validator's actionable message must reach the next call so the model can
    # self-correct (mirrors AL-031's retry-message assertion).
    agent = build_lesson_agent()
    deps = _deps()
    respond = LessonResponder(
        [_valid_content_dict(read_passage=_passage(10)), _valid_content_dict()]
    )
    result = agent.run_sync(
        build_lesson_prompt(deps), deps=deps, model=FunctionModel(respond)
    ).output
    assert isinstance(result, LessonContent)
    assert respond.call_count == 2
    retry_text = _retry_prompt_text(respond.messages_per_call[1])
    assert "word" in retry_text.lower()


def test_agent_stops_after_retry_budget_when_model_never_complies() -> None:
    # A model that violates on every call must terminate: Agent(retries=3) bounds
    # output-validation retries, so 1 initial + 3 retries = 4 calls, then raises.
    agent = build_lesson_agent()
    deps = _deps()
    respond = LessonResponder([_valid_content_dict(read_passage=_passage(10))])
    with pytest.raises(UnexpectedModelBehavior):
        agent.run_sync(
            build_lesson_prompt(deps), deps=deps, model=FunctionModel(respond)
        )
    assert respond.call_count == 4


# --- the AL-030 stub drives the real agent (§12) -------------------------------


def test_stub_lesson_passes_the_real_validators() -> None:
    # The deterministic stub's lesson output must satisfy the assembled agent's
    # validators unchanged (the CI/e2e contract, §12): the run below only succeeds
    # if the agent's own output_validator accepted it, and we re-assert explicitly.
    agent = build_lesson_agent()
    deps = _deps(topic="US healthcare payment", position=2)
    result = agent.run_sync(
        build_lesson_prompt(deps), deps=deps, model=build_stub_model()
    ).output
    assert isinstance(result, LessonContent)
    assert validate_lesson_content(deps.caps, result) is result


def test_stub_force_lesson_failure_raises_through_the_real_agent() -> None:
    # [force-lesson-failure:N] fires when the prompt's position_in_path == N,
    # proving the position contract holds end-to-end through our assembly.
    agent = build_lesson_agent()
    deps = _deps(topic=f"Rust ownership {force_lesson_failure(4)}", position=4)
    with pytest.raises(StubModelForcedError):
        agent.run_sync(build_lesson_prompt(deps), deps=deps, model=build_stub_model())


def test_stub_force_lesson_failure_only_fires_at_matching_position() -> None:
    # Same sentinel N=4, but generating position 2: the stub must NOT fail, which
    # only holds if our prompt carries the true position (2), not the sentinel's N.
    agent = build_lesson_agent()
    deps = _deps(topic=f"Rust ownership {force_lesson_failure(4)}", position=2)
    result = agent.run_sync(
        build_lesson_prompt(deps), deps=deps, model=build_stub_model()
    ).output
    assert isinstance(result, LessonContent)
