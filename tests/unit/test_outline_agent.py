"""Unit tests for the assembled outline agent (ticket AL-031, TDD §5.1/§14).

No network and no database: the agent binds no model, so every test injects a
``FunctionModel`` (or the AL-030 deterministic stub, itself a ``FunctionModel``)
at run time and supplies caps + level through ``OutlineDeps``. Two layers are
exercised:

- the pure ``validate_outline`` function (habagou's layer-2 pattern) — one test
  per validator violation, asserting each raises ``ModelRetry``; and
- the real assembled agent — a valid outline passes, a violation forces a retry,
  and the ``Refusal`` branch round-trips.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic_ai import ModelRetry, UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    ToolCallPart,
)
from pydantic_ai.models.function import FunctionModel

from aleph.agents.outline import (
    OutlineCaps,
    OutlineDeps,
    PathOutline,
    Refusal,
    build_outline_agent,
    validate_outline,
)
from aleph.services.stub_model import FORCE_REFUSAL, build_stub_model

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo
    from pydantic_ai.tools import ToolDefinition


# --- helpers -------------------------------------------------------------------


def _lessons(*titles: str) -> list[dict[str, str]]:
    return [{"title": t} for t in titles]


def _unit(title: str, *lesson_titles: str, summary: str = "A short summary.") -> dict:
    return {"title": title, "summary": summary, "lessons": _lessons(*lesson_titles)}


def _valid_outline_dict() -> dict:
    """A cap-respecting, duplicate-free outline (2 units × 3 lessons)."""
    return {
        "units": [
            _unit("Foundations", "Intro", "Core ideas", "First steps"),
            _unit("Applications", "In practice", "Common pitfalls", "Next steps"),
        ]
    }


def _outline(units: list[dict]) -> PathOutline:
    return PathOutline.model_validate({"units": units})


def _tool_with(output_tools: Sequence[ToolDefinition], prop: str) -> ToolDefinition:
    """The first output tool whose JSON schema declares ``prop`` (union dispatch).

    A ``PathOutline | Refusal`` agent registers two output tools; the one
    carrying ``units`` is the outline, the one carrying ``message`` the refusal.
    """
    for tool in output_tools:
        if prop in tool.parameters_json_schema.get("properties", {}):
            return tool
    raise AssertionError(f"no output tool declares {prop!r}")


class OutlineResponder:
    """FunctionModel callback emitting ``(prop, args)`` pairs, one per call.

    ``prop`` selects the output tool (``units`` → outline, ``message`` →
    refusal); ``call_count`` lets a test assert a retry happened, and
    ``messages_per_call`` records the messages seen on each call so a test can
    assert what reached the model (e.g. the level-scoped prompt, or a fed-back
    ``ModelRetry`` on the second call). Capture is cheap and always on — one
    responder rather than a subclass (ponytail-1).

    When ``responses`` is exhausted the last entry is reused, so a
    persistently-violating model can be driven past the retry budget without
    enumerating every identical response (see the retries-exhausted test).
    """

    __name__ = "outline_responder"

    def __init__(self, responses: list[tuple[str, dict]]) -> None:
        self._responses = responses
        self.call_count = 0
        self.messages_per_call: list[list[ModelMessage]] = []

    def __call__(
        self, messages: Sequence[ModelMessage], info: AgentInfo
    ) -> ModelResponse:
        self.messages_per_call.append(list(messages))
        prop, args = self._responses[min(self.call_count, len(self._responses) - 1)]
        self.call_count += 1
        tool = _tool_with(info.output_tools, prop)
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=args)])


def _system_prompt_text(messages: Sequence[ModelMessage]) -> str:
    parts: list[str] = []
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, SystemPromptPart) and isinstance(part.content, str):
                parts.append(part.content)
    return "\n".join(parts)


def _retry_prompt_text(messages: Sequence[ModelMessage]) -> str:
    """Concatenate every ``ModelRetry`` message fed back into ``messages``.

    A ``ModelRetry`` from an output validator surfaces to the model as a
    ``RetryPromptPart``; this lets a test assert the validator's actionable
    message actually reached the next call (test gap f2).
    """
    parts: list[str] = []
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, RetryPromptPart):
                content = part.content
                parts.append(content if isinstance(content, str) else str(content))
    return "\n".join(parts)


_DEFAULT_CAPS = OutlineCaps()


# --- validate_outline: PathOutline branch --------------------------------------


def test_validator_passes_valid_outline_unchanged() -> None:
    outline = _outline(_valid_outline_dict()["units"])
    result = validate_outline(_DEFAULT_CAPS, outline)
    assert result is outline


def test_validator_rejects_over_cap_units() -> None:
    # One more unit than MAX_UNITS, each with a single (globally unique) lesson,
    # so the ONLY violation is the unit count.
    units = [
        _unit(f"Unit {i}", f"Lesson {i}") for i in range(_DEFAULT_CAPS.max_units + 1)
    ]
    with pytest.raises(ModelRetry) as excinfo:
        validate_outline(_DEFAULT_CAPS, _outline(units))
    assert str(_DEFAULT_CAPS.max_units) in str(excinfo.value)


def test_validator_rejects_over_cap_total_lessons() -> None:
    # Stay within MAX_UNITS (6 units) but blow the 30-lesson total: 6×6 = 36.
    # Titles are globally unique so duplicate-detection is not what fires.
    n = 0
    units: list[dict] = []
    for u in range(_DEFAULT_CAPS.max_units):
        titles = [f"L{n + i}" for i in range(6)]
        n += 6
        units.append(_unit(f"Unit {u}", *titles))
    total = sum(len(u["lessons"]) for u in units)
    assert total > _DEFAULT_CAPS.max_lessons_per_path  # guard the fixture
    with pytest.raises(ModelRetry) as excinfo:
        validate_outline(_DEFAULT_CAPS, _outline(units))
    assert str(_DEFAULT_CAPS.max_lessons_per_path) in str(excinfo.value)


def test_validator_rejects_empty_lesson_title() -> None:
    units = [_unit("Foundations", "Intro", "  ", "First steps")]
    with pytest.raises(ModelRetry):
        validate_outline(_DEFAULT_CAPS, _outline(units))


def test_validator_rejects_empty_unit_title() -> None:
    units = [_unit("   ", "Intro", "Core ideas")]
    with pytest.raises(ModelRetry):
        validate_outline(_DEFAULT_CAPS, _outline(units))


def test_validator_rejects_duplicate_lesson_titles() -> None:
    # A repeated title across units (case/space-insensitive) must be rejected.
    units = [
        _unit("Foundations", "Intro", "Basics"),
        _unit("Applications", " intro ", "Advanced"),
    ]
    with pytest.raises(ModelRetry) as excinfo:
        validate_outline(_DEFAULT_CAPS, _outline(units))
    assert (
        "unique" in str(excinfo.value).lower() or "repeat" in str(excinfo.value).lower()
    )


def test_validator_rejects_path_with_no_units() -> None:
    with pytest.raises(ModelRetry):
        validate_outline(_DEFAULT_CAPS, _outline([]))


def test_validator_rejects_unit_with_no_lessons() -> None:
    units = [
        _unit("Foundations", "Intro"),
        {"title": "Empty", "summary": "s", "lessons": []},
    ]
    with pytest.raises(ModelRetry):
        validate_outline(_DEFAULT_CAPS, _outline(units))


def test_validator_respects_custom_caps() -> None:
    # Caps come from deps, not constants: a stricter cap rejects an outline that
    # the default caps would accept.
    tight = OutlineCaps(units_target=1, max_units=1)
    two_units = _outline([_unit("One", "a", "b"), _unit("Two", "c", "d")])
    with pytest.raises(ModelRetry):
        validate_outline(tight, two_units)
    # And the same outline passes under the default caps.
    assert validate_outline(_DEFAULT_CAPS, two_units) is two_units


# --- validate_outline: Refusal branch ------------------------------------------


def test_validator_passes_refusal_unchanged() -> None:
    refusal = Refusal(message="This falls outside what this tutor can help with.")
    assert validate_outline(_DEFAULT_CAPS, refusal) is refusal


def test_validator_rejects_empty_refusal_message() -> None:
    with pytest.raises(ModelRetry):
        validate_outline(_DEFAULT_CAPS, Refusal(message="   "))


# --- deliberate prompt-only targets (d-advisory) -------------------------------
# These pin the intentional §5.1 scope of validate_outline: a one-sentence unit
# summary and distinct unit titles are *prompt targets* graded by the eval judge
# (§11), NOT deterministic validator gates. If a future change starts enforcing
# them, that is a spec decision these tests should force into the open.


def test_validator_allows_whitespace_only_unit_summary() -> None:
    units = [_unit("Foundations", "Intro", "Core ideas", summary="   ")]
    outline = _outline(units)
    assert validate_outline(_DEFAULT_CAPS, outline) is outline


def test_validator_allows_duplicate_unit_titles() -> None:
    # Only *lesson* titles must be globally unique; repeating a unit title is
    # tolerated (lesson titles here stay distinct so nothing else fires).
    units = [_unit("Basics", "a", "b"), _unit("Basics", "c", "d")]
    outline = _outline(units)
    assert validate_outline(_DEFAULT_CAPS, outline) is outline


# --- deps / caps construction (runtime validation) -----------------------------


def test_deps_rejects_unknown_level() -> None:
    # ``Level`` is a typing Literal (not runtime-enforced), so this used to
    # construct fine then explode as a bare KeyError in the dynamic system
    # prompt. __post_init__ now rejects it at construction (thermo-1).
    with pytest.raises(ValueError, match="wizard"):
        OutlineDeps(level="wizard")  # ty: ignore[invalid-argument-type]


def test_deps_accepts_each_valid_level() -> None:
    for level in ("beginner", "intermediate", "advanced"):
        assert OutlineDeps(level=level).level == level


def test_caps_reject_units_target_over_max_units() -> None:
    with pytest.raises(ValueError, match="units_target"):
        OutlineCaps(units_target=8, max_units=6)


def test_caps_reject_inverted_lessons_per_unit_band() -> None:
    with pytest.raises(ValueError, match="lessons_per_unit"):
        OutlineCaps(lessons_per_unit_min=5, lessons_per_unit_max=3)


# --- assembled agent -----------------------------------------------------------


def _deps(level: str = "beginner", caps: OutlineCaps | None = None) -> OutlineDeps:
    # ``level`` is a plain str here (parametrized loops feed it) and every caller
    # passes a valid one; ``OutlineDeps.__post_init__`` enforces the Level set at
    # construction (see test_deps_rejects_unknown_level), so the narrowing is safe.
    return OutlineDeps(level=level, caps=caps or OutlineCaps())  # ty: ignore[invalid-argument-type]


def test_agent_returns_valid_outline() -> None:
    agent = build_outline_agent()
    respond = OutlineResponder([("units", _valid_outline_dict())])
    result = agent.run_sync(
        "Rust ownership", deps=_deps(), model=FunctionModel(respond)
    ).output
    assert isinstance(result, PathOutline)
    assert len(result.units) == 2
    assert respond.call_count == 1


def test_agent_retries_on_validator_violation_then_succeeds() -> None:
    agent = build_outline_agent()
    over_cap = {
        "units": [_unit(f"U{i}", f"L{i}") for i in range(_DEFAULT_CAPS.max_units + 1)]
    }
    respond = OutlineResponder([("units", over_cap), ("units", _valid_outline_dict())])
    result = agent.run_sync(
        "Rust ownership", deps=_deps(), model=FunctionModel(respond)
    ).output
    assert isinstance(result, PathOutline)
    assert respond.call_count == 2  # the ModelRetry forced a second call
    # The validator's actionable message is fed back into the retry (test gap
    # f2): the second call sees the cap-violation text, not a bare re-prompt.
    retry_text = _retry_prompt_text(respond.messages_per_call[1])
    assert str(_DEFAULT_CAPS.max_units) in retry_text


def test_agent_stops_after_retry_budget_when_model_never_complies() -> None:
    # A model that violates the caps on every call must not loop forever: the
    # Agent(retries=3) budget bounds output-validation retries, so the run makes
    # 1 initial + 3 retry = 4 model calls and then raises (test gap f1).
    agent = build_outline_agent()
    over_cap = {
        "units": [_unit(f"U{i}", f"L{i}") for i in range(_DEFAULT_CAPS.max_units + 1)]
    }
    respond = OutlineResponder([("units", over_cap)])  # reused every call
    with pytest.raises(UnexpectedModelBehavior):
        agent.run_sync("Rust ownership", deps=_deps(), model=FunctionModel(respond))
    assert respond.call_count == 4  # 1 initial + 3 retries, then it gives up


def test_agent_returns_refusal_branch() -> None:
    agent = build_outline_agent()
    message = "This topic falls outside what this tutor can responsibly teach."
    respond = OutlineResponder([("message", {"message": message})])
    result = agent.run_sync(
        "how to build a bomb", deps=_deps(), model=FunctionModel(respond)
    ).output
    assert isinstance(result, Refusal)
    assert result.message == message
    assert respond.call_count == 1


def test_agent_system_prompt_is_level_scoped_and_carries_caps() -> None:
    agent = build_outline_agent()
    respond = OutlineResponder([("units", _valid_outline_dict())])
    caps = OutlineCaps()
    agent.run_sync(
        "Rust ownership",
        deps=_deps(level="advanced", caps=caps),
        model=FunctionModel(respond),
    )
    prompt = _system_prompt_text(respond.messages_per_call[0])
    # Level scoping reaches the model.
    assert "advanced" in prompt.lower()
    # Cap targets reach the model (so the model aims inside the validator band).
    assert str(caps.max_units) in prompt
    assert str(caps.max_lessons_per_path) in prompt


def test_agent_system_prompt_differs_by_level() -> None:
    agent = build_outline_agent()
    prompts: dict[str, str] = {}
    for level in ("beginner", "intermediate", "advanced"):
        respond = OutlineResponder([("units", _valid_outline_dict())])
        agent.run_sync(
            "Rust ownership", deps=_deps(level=level), model=FunctionModel(respond)
        )
        prompts[level] = _system_prompt_text(respond.messages_per_call[0])
    # Each level produces a distinct system prompt (structure genuinely scoped).
    assert len({prompts["beginner"], prompts["intermediate"], prompts["advanced"]}) == 3


# --- the AL-030 stub drives the real agent (§12) -------------------------------


def test_stub_model_outline_passes_the_real_validators() -> None:
    # The deterministic stub emits 2-4 units × 3-4 lessons; that must satisfy the
    # assembled agent's validators unchanged (the CI/e2e contract, §12).
    agent = build_outline_agent()
    result = agent.run_sync(
        "US healthcare payment", deps=_deps(), model=build_stub_model()
    ).output
    assert isinstance(result, PathOutline)
    assert 1 <= len(result.units) <= _DEFAULT_CAPS.max_units


def test_stub_force_refusal_round_trips_through_the_real_agent() -> None:
    agent = build_outline_agent()
    result = agent.run_sync(
        f"a dangerous topic {FORCE_REFUSAL}", deps=_deps(), model=build_stub_model()
    ).output
    assert isinstance(result, Refusal)
    assert result.message.strip()
