"""Unit tests for the stub model's *shaping* sentinels (Phase 2B TDD §11, D12).

Phase 2's streamed stub (``tests/unit/test_stub_model_stream.py``, untouched
here) answers tutor turns. Phase 2B's shaping conversation runs the very same
streamed branch, so the stub gains four question-text sentinels — stateless,
stripped from output, exactly the Phase 1/2 discipline — that make W17-W21
repeatable without a real model choosing to call ``propose_path_edit``.

These tests drive the stream function **directly**, because ``agents/shaper.py``
(AL-310) does not exist yet and the stub deliberately emits its tool call *by
name* (the AL-202 arrangement for ``pose_tutor_check``, which later became an
import). One test runs a throwaway agent with a same-shaped ``propose_path_edit``
tool registered, to prove the emitted deltas really do assemble into a tool call
carrying a schema-valid payload.

**Proposal validity is asserted structurally here** — the D1 predicates
(``operations_within_caps``, ``insertions_after_first_shapeable``,
``revision_targets_unengaged``, ``titles_nonempty_distinct``) land with AL-310
and are meant to replace :func:`_assert_valid_proposal` below, never to be
copied into it.

New file (AL-302).
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, DeltaToolCall

from aleph.agents.lesson import (
    LessonContent,
    is_non_empty,
    passage_within_word_band,
)
from aleph.services.stub_model import (
    FORCE_LESSON_ERROR,
    FORCE_PROPOSAL_ADD,
    FORCE_PROPOSAL_REVISE,
    FORCE_SHAPING_DECLINE,
    FORCE_SHAPING_FAILURE,
    FORCE_TUTOR_FAILURE,
    PROPOSE_PATH_EDIT_TOOL_NAME,
    REVISED_PASSAGE_MARKER,
    SHAPING_DECLINED_EDIT_REPLY,
    SHAPING_REVISION_INSTRUCTION,
    TUTOR_REFUSAL_REPLY,
    StubModelForcedError,
    build_stub_addition_proposal,
    build_stub_model,
    build_stub_revision_proposal,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.messages import ModelMessage


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# The streamed reply emits "several" deltas (Phase 2's number), enough that
# `[force-shaping-failure]` fires mid-stream with deltas still owed.
_MIN_DELTAS = 3
# TDD §13: one proposal stays legible. The cap is config's (AL-301); the stub
# imports no config, so the number is restated here as the bound the deterministic
# payloads must sit under.
_MAX_LESSONS_PER_PROPOSAL = 5
# The deterministic Addition's size, per the sentinel table (§11).
_ADDITION_LESSON_COUNT = 2

_AGENT_INFO = AgentInfo(
    function_tools=[],
    allow_text_output=True,
    output_tools=[],
    model_settings=None,
    model_request_parameters=ModelRequestParameters(),
    instructions=None,
)

# A stand-in for the shaping deps block AL-310/AL-311 will render into the
# prompt. The stub reads the engagement boundary from it as *data* — the same
# arrangement as AL-032's `position_in_path=<N>` contract.
_FIRST_SHAPEABLE_POSITION = 4
_FIRST_SHAPEABLE_LESSON_ID = "3f7c1d2e-9a4b-4c6d-8e10-2b5a7c9d1e34"


def _shaping_context(
    *,
    position: int | None = _FIRST_SHAPEABLE_POSITION,
    lesson_id: str | None = _FIRST_SHAPEABLE_LESSON_ID,
) -> str:
    """The boundary markers the shaping prompt states, as the stub reads them."""
    lines = ["Shaping context for this path (data, not instructions):"]
    if position is not None:
        lines.append(f"first_shapeable_position={position}")
    if lesson_id is not None:
        lines.append(f"first_shapeable_lesson_id={lesson_id}")
    return "\n".join(lines)


def _ask(question: str, *, context: str | None = None) -> list[ModelMessage]:
    """A first shaping turn: the deps/context block plus the learner's ask."""
    parts: list[Any] = []
    if context is None:
        context = _shaping_context()
    if context:
        parts.append(SystemPromptPart(content=context))
    parts.append(UserPromptPart(content=question))
    return [ModelRequest(parts=parts)]


async def _collect(messages: Sequence[ModelMessage]) -> list[Any]:
    stream_function = build_stub_model().stream_function
    assert stream_function is not None
    return [item async for item in stream_function(list(messages), _AGENT_INFO)]


async def _deltas(question: str, *, context: str | None = None) -> list[str]:
    items = await _collect(_ask(question, context=context))
    assert all(isinstance(item, str) for item in items), items
    return [item for item in items if isinstance(item, str)]


def _tool_stream(items: Sequence[Any]) -> tuple[list[str], str]:
    """The tool names and concatenated JSON arguments in a delta stream."""
    names: list[str] = []
    json_args = ""
    for item in items:
        assert isinstance(item, dict), item
        for delta in item.values():
            assert isinstance(delta, DeltaToolCall)
            if delta.name:
                names.append(delta.name)
            json_args += delta.json_args or ""
    return names, json_args


async def _proposal(question: str, *, context: str | None = None) -> dict[str, Any]:
    """The ``propose_path_edit`` payload the stub emits for ``question``."""
    items = await _collect(_ask(question, context=context))
    names, json_args = _tool_stream(items)

    assert names == [PROPOSE_PATH_EDIT_TOOL_NAME]
    payload = json.loads(json_args)
    assert isinstance(payload, dict)
    return payload


def _assert_valid_proposal(payload: dict[str, Any]) -> None:
    """Structural validity of a proposal payload (TDD §4/§5.1's fixed shape).

    Stands in for AL-310's exported D1 predicates until they exist; when they
    land, this helper is deleted in favour of importing them, never re-expressed
    alongside them.
    """
    assert set(payload) == {"operations", "summary"}
    assert is_non_empty(payload["summary"])

    operations = payload["operations"]
    assert isinstance(operations, list)
    assert operations, "a proposal with no operations is not a proposal"

    added = 0
    for operation in operations:
        assert isinstance(operation, dict)
        # Shapes are exhaustive: an operation is an Addition or a Revision.
        if "lessons" in operation:
            assert set(operation) == {
                "insert_at_position",
                "new_unit",
                "lessons",
                "rationale",
                "estimated_minutes",
            }
            lessons = operation["lessons"]
            assert lessons
            titles = [lesson["title"] for lesson in lessons]
            assert all(is_non_empty(title) for title in titles)
            assert len(set(titles)) == len(titles)
            assert isinstance(operation["insert_at_position"], int)
            assert operation["insert_at_position"] >= 1
            new_unit = operation["new_unit"]
            assert new_unit is None or set(new_unit) == {"title", "summary"}
            assert is_non_empty(operation["rationale"])
            assert isinstance(operation["estimated_minutes"], int)
            assert operation["estimated_minutes"] > 0
            added += len(lessons)
        else:
            assert set(operation) == {
                "lesson_id",
                "instruction",
                "new_title",
                "rationale",
            }
            assert is_non_empty(operation["lesson_id"])
            assert is_non_empty(operation["instruction"])
            assert operation["new_title"] is None or is_non_empty(
                operation["new_title"]
            )
            assert is_non_empty(operation["rationale"])

    assert added <= _MAX_LESSONS_PER_PROPOSAL


# --- lesson-branch fixtures (the revision marker rides in via the prompt) -------


def _lesson_agent() -> Agent[None, LessonContent]:
    return Agent[None, LessonContent](
        output_type=LessonContent, model=build_stub_model()
    )


def _lesson_prompt(topic: str, position: int, *, revision: str | None = None) -> str:
    # D7: apply stores `revision_instruction` and the Phase 1 lesson prompt gains
    # a revision block carrying it. The stub reads that block's instruction.
    block = f"\nrevision_instruction: {revision}" if revision else ""
    return f"topic={topic}\nposition_in_path={position}{block}\nGenerate the lesson."


def _lesson(
    topic: str, position: int = 1, *, revision: str | None = None
) -> LessonContent:
    prompt = _lesson_prompt(topic, position, revision=revision)
    return _lesson_agent().run_sync(prompt).output


# --- [force-proposal-add] -------------------------------------------------------


@pytest.mark.anyio
async def test_force_proposal_add_emits_a_valid_two_lesson_addition() -> None:
    payload = await _proposal(f"add a couple of lessons on this {FORCE_PROPOSAL_ADD}")

    _assert_valid_proposal(payload)
    (operation,) = payload["operations"]
    assert len(operation["lessons"]) == _ADDITION_LESSON_COUNT


@pytest.mark.anyio
async def test_force_proposal_add_inserts_at_the_first_shapeable_position() -> None:
    # The engagement boundary (D2) is the whole point of the Addition shape: new
    # lessons land at or after the learner's first non-engaged position.
    payload = await _proposal(f"add two lessons {FORCE_PROPOSAL_ADD}")

    (operation,) = payload["operations"]
    assert operation["insert_at_position"] == _FIRST_SHAPEABLE_POSITION


@pytest.mark.anyio
async def test_force_proposal_add_follows_the_stated_boundary() -> None:
    payload = await _proposal(
        f"add two lessons {FORCE_PROPOSAL_ADD}",
        context=_shaping_context(position=9),
    )

    (operation,) = payload["operations"]
    assert operation["insert_at_position"] == 9


@pytest.mark.anyio
async def test_force_proposal_add_is_deterministic() -> None:
    question = f"add a couple of lessons on this {FORCE_PROPOSAL_ADD}"
    first = await _collect(_ask(question))
    second = await _collect(_ask(question))

    # Identical *sequence* of deltas, and an identical payload.
    assert _tool_stream(first) == _tool_stream(second)
    assert len(first) == len(second)


@pytest.mark.anyio
async def test_force_proposal_add_emits_several_argument_deltas() -> None:
    # Emitted in pieces so the service's tool-argument accumulation is exercised,
    # not just a single whole-payload part.
    items = await _collect(_ask(f"add two lessons {FORCE_PROPOSAL_ADD}"))

    assert len(items) >= 2


@pytest.mark.anyio
async def test_force_proposal_add_without_a_stated_boundary_raises() -> None:
    # Presence is mandatory, not optional — the AL-032 posture. A silent default
    # would emit an Addition *before* the boundary on every engaged path, which
    # the predicates would then reject for reasons that look like agent error.
    stream_function = build_stub_model().stream_function
    assert stream_function is not None
    messages = _ask(f"add two lessons {FORCE_PROPOSAL_ADD}", context="no boundary here")

    with pytest.raises(StubModelForcedError, match="first_shapeable_position"):
        async for _ in stream_function(messages, _AGENT_INFO):
            pass


@pytest.mark.anyio
async def test_a_json_rendered_deps_block_still_states_the_boundary() -> None:
    # Plain `name=value` lines are the contract, but a downstream ticket that
    # renders the deps block as JSON must not trip the mandatory markers — they
    # would then fail loudly for a reason that looks like a stub bug.
    context = json.dumps(
        {
            "first_shapeable_position": _FIRST_SHAPEABLE_POSITION,
            "first_shapeable_lesson_id": _FIRST_SHAPEABLE_LESSON_ID,
        }
    )
    added = await _proposal(f"add two lessons {FORCE_PROPOSAL_ADD}", context=context)
    revised = await _proposal(
        f"make that simpler {FORCE_PROPOSAL_REVISE}", context=context
    )

    assert added["operations"][0]["insert_at_position"] == _FIRST_SHAPEABLE_POSITION
    assert revised["operations"][0]["lesson_id"] == _FIRST_SHAPEABLE_LESSON_ID


def test_addition_payload_builder_is_deterministic_and_ask_sensitive() -> None:
    payload = build_stub_addition_proposal("add two lessons", insert_at_position=4)

    assert payload == build_stub_addition_proposal(
        "add two lessons", insert_at_position=4
    )
    assert payload != build_stub_addition_proposal(
        "something else entirely", insert_at_position=4
    )
    _assert_valid_proposal(json.loads(json.dumps(payload)))


# --- [force-proposal-revise] ----------------------------------------------------


@pytest.mark.anyio
async def test_force_proposal_revise_targets_the_first_unengaged_lesson() -> None:
    payload = await _proposal(f"make that lesson simpler {FORCE_PROPOSAL_REVISE}")

    _assert_valid_proposal(payload)
    (operation,) = payload["operations"]
    assert operation["lesson_id"] == _FIRST_SHAPEABLE_LESSON_ID
    # The instruction is what rides into `revision_instruction` at apply, and is
    # what the lesson stub recognises when it regenerates (W18's structural link).
    assert operation["instruction"] == SHAPING_REVISION_INSTRUCTION


@pytest.mark.anyio
async def test_force_proposal_revise_follows_the_stated_target() -> None:
    other = str(uuid4())
    payload = await _proposal(
        f"make that lesson simpler {FORCE_PROPOSAL_REVISE}",
        context=_shaping_context(lesson_id=other),
    )

    (operation,) = payload["operations"]
    assert operation["lesson_id"] == other


@pytest.mark.anyio
async def test_force_proposal_revise_is_deterministic() -> None:
    question = f"make that lesson simpler {FORCE_PROPOSAL_REVISE}"
    first = await _collect(_ask(question))
    second = await _collect(_ask(question))

    assert _tool_stream(first) == _tool_stream(second)
    assert len(first) == len(second)


@pytest.mark.anyio
async def test_force_proposal_revise_without_a_stated_target_raises() -> None:
    stream_function = build_stub_model().stream_function
    assert stream_function is not None
    messages = _ask(
        f"make that simpler {FORCE_PROPOSAL_REVISE}",
        context=_shaping_context(lesson_id=None),
    )

    with pytest.raises(StubModelForcedError, match="first_shapeable_lesson_id"):
        async for _ in stream_function(messages, _AGENT_INFO):
            pass


def test_revision_payload_builder_is_deterministic_and_ask_sensitive() -> None:
    lesson_id = _FIRST_SHAPEABLE_LESSON_ID
    payload = build_stub_revision_proposal("make it simpler", lesson_id=lesson_id)

    assert payload == build_stub_revision_proposal(
        "make it simpler", lesson_id=lesson_id
    )
    assert payload != build_stub_revision_proposal("go deeper", lesson_id=lesson_id)
    _assert_valid_proposal(json.loads(json.dumps(payload)))


# --- the revision marker in the regenerated passage (W18) -----------------------


def test_a_revised_lesson_passage_embeds_the_revision_marker() -> None:
    lesson = _lesson("Rust ownership", 3, revision=SHAPING_REVISION_INSTRUCTION)

    assert REVISED_PASSAGE_MARKER in lesson.read_passage


def test_a_rewrapped_revision_block_still_lands_the_marker() -> None:
    # W18's link survives layout: AL-321 owns how the revision block is wrapped
    # and indented, and the stub compares on whitespace-collapsed text. Only the
    # words are contractual.
    rewrapped = "\n    ".join(SHAPING_REVISION_INSTRUCTION.split())
    lesson = _lesson("Rust ownership", 3, revision=rewrapped)

    assert REVISED_PASSAGE_MARKER in lesson.read_passage


def test_a_paraphrased_revision_block_does_not_land_the_marker() -> None:
    # The other half of the contract: whitespace is free, the words are not.
    lesson = _lesson("Rust ownership", 3, revision="Please make this one simpler.")

    assert REVISED_PASSAGE_MARKER not in lesson.read_passage


def test_a_lesson_generated_without_a_revision_carries_no_marker() -> None:
    lesson = _lesson("Rust ownership", 3)

    assert REVISED_PASSAGE_MARKER not in lesson.read_passage


def test_a_revised_lesson_passage_stays_within_the_word_band() -> None:
    long_topic = (
        "advanced distributed systems consensus and replication under partial "
        "failure in geographically dispersed clusters"
    )
    lesson = _lesson(long_topic, 2, revision=SHAPING_REVISION_INSTRUCTION)

    assert passage_within_word_band(lesson.read_passage)


def test_a_revised_lesson_with_the_error_sentinel_stays_within_the_word_band() -> None:
    # Both substitutions at once: the marker replaces a body paragraph rather
    # than adding one, so the §14 band holds however they combine.
    lesson = _lesson(
        f"boiling points {FORCE_LESSON_ERROR}", 2, revision=SHAPING_REVISION_INSTRUCTION
    )

    assert passage_within_word_band(lesson.read_passage)
    assert REVISED_PASSAGE_MARKER in lesson.read_passage


def test_a_revised_lesson_is_deterministic() -> None:
    first = _lesson("Rust ownership", 3, revision=SHAPING_REVISION_INSTRUCTION)
    second = _lesson("Rust ownership", 3, revision=SHAPING_REVISION_INSTRUCTION)

    assert first == second


# --- [force-shaping-decline] ----------------------------------------------------


@pytest.mark.anyio
async def test_force_shaping_decline_streams_the_declined_edit_wording() -> None:
    deltas = await _deltas(f"delete the decorators lesson {FORCE_SHAPING_DECLINE}")

    assert len(deltas) >= _MIN_DELTAS
    assert "".join(deltas) == SHAPING_DECLINED_EDIT_REPLY


@pytest.mark.anyio
async def test_force_shaping_decline_is_deterministic() -> None:
    question = f"reorder my units {FORCE_SHAPING_DECLINE}"

    assert await _deltas(question) == await _deltas(question)


def test_declined_edit_wording_names_what_shaping_can_do() -> None:
    # CONTEXT.md: a declined edit "names what shaping can do", and its wording is
    # distinct from both a failure and a safety refusal (W20's assertion target).
    lowered = SHAPING_DECLINED_EDIT_REPLY.lower()

    assert lowered.strip()
    assert "add" in lowered
    assert "revise" in lowered
    assert "went wrong" not in lowered
    assert "try again" not in lowered
    assert SHAPING_DECLINED_EDIT_REPLY != TUTOR_REFUSAL_REPLY


# --- [force-shaping-failure] ----------------------------------------------------


@pytest.mark.anyio
async def test_force_shaping_failure_raises_after_at_least_two_deltas() -> None:
    stream_function = build_stub_model().stream_function
    assert stream_function is not None
    messages = _ask(f"add something {FORCE_SHAPING_FAILURE}")

    seen: list[Any] = []
    with pytest.raises(StubModelForcedError, match="forced shaping failure"):
        async for item in stream_function(messages, _AGENT_INFO):
            seen.append(item)

    # Mid-stream, not before the first byte: the discard-partial path needs a
    # partial to discard.
    assert len(seen) >= 2
    assert all(isinstance(item, str) for item in seen)


@pytest.mark.anyio
async def test_force_shaping_failure_is_stateless() -> None:
    for _ in range(2):
        with pytest.raises(StubModelForcedError, match="forced shaping failure"):
            await _deltas(f"add something {FORCE_SHAPING_FAILURE}")


# --- the leg after a proposal ---------------------------------------------------


@pytest.mark.anyio
async def test_the_leg_after_a_proposal_streams_text() -> None:
    # Stateless detection, as for `pose_tutor_check`: the tool call already in
    # this run's messages is what tells the stub the proposal is made, so the
    # follow-up leg streams the accompanying reply rather than proposing twice.
    question = f"add two lessons {FORCE_PROPOSAL_ADD}"
    call_id = "propose-path-edit-1"
    payload = build_stub_addition_proposal(
        # The stub seeds from the *cleaned* question, sentinels stripped.
        re.sub(re.escape(FORCE_PROPOSAL_ADD), "", question).strip(),
        insert_at_position=_FIRST_SHAPEABLE_POSITION,
    )
    messages: list[ModelMessage] = [
        *_ask(question),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=PROPOSE_PATH_EDIT_TOOL_NAME,
                    args=dict(payload),
                    tool_call_id=call_id,
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name=PROPOSE_PATH_EDIT_TOOL_NAME,
                    content="proposal recorded",
                    tool_call_id=call_id,
                )
            ]
        ),
    ]

    items = await _collect(messages)

    assert len(items) >= _MIN_DELTAS
    assert all(isinstance(item, str) for item in items)
    assert "".join(items).strip()


def test_proposal_assembles_into_a_tool_call_on_a_real_agent() -> None:
    # The stub emits the call *by name* (agents/shaper.py is AL-310's), so this
    # registers a same-shaped throwaway tool to prove the deltas really do
    # assemble into one tool call carrying the fixed payload shape.
    calls: list[tuple[list[dict[str, Any]], str]] = []
    agent = Agent[None, str](output_type=str, model=build_stub_model())

    @agent.tool_plain
    def propose_path_edit(operations: list[dict[str, Any]], summary: str) -> str:
        calls.append((operations, summary))
        return "Proposal recorded."

    prompt = f"add two lessons {FORCE_PROPOSAL_ADD}\n{_shaping_context()}"
    with agent.run_stream_sync(prompt) as result:
        output = result.get_output()

    assert len(calls) == 1
    operations, summary = calls[0]
    _assert_valid_proposal({"operations": operations, "summary": summary})
    assert output.strip()
    assert FORCE_PROPOSAL_ADD not in output


# --- the in-lesson tutor is untouched (W21) ------------------------------------


@pytest.mark.anyio
async def test_a_question_without_a_shaping_sentinel_still_streams_a_reply() -> None:
    deltas = await _deltas("what would you add to this path?")

    assert len(deltas) >= _MIN_DELTAS
    assert "".join(deltas).strip()
    assert SHAPING_DECLINED_EDIT_REPLY not in "".join(deltas)


@pytest.mark.anyio
async def test_a_tutor_failure_sentinel_still_reports_as_a_tutor_failure() -> None:
    # The two failure sentinels are distinguishable: 2A's message is unchanged.
    with pytest.raises(StubModelForcedError, match="forced tutor failure"):
        await _deltas(f"explain this {FORCE_TUTOR_FAILURE}")


# --- sentinels never leak into the output --------------------------------------


@pytest.mark.parametrize(
    "sentinel",
    [FORCE_PROPOSAL_ADD, FORCE_PROPOSAL_REVISE, FORCE_SHAPING_DECLINE],
)
@pytest.mark.anyio
async def test_sentinel_text_never_appears_in_the_streamed_output(
    sentinel: str,
) -> None:
    question = f"shape my path please {sentinel}"
    items = await _collect(_ask(question))

    emitted = "".join(
        item
        if isinstance(item, str)
        else "".join(delta.json_args or "" for delta in item.values())
        for item in items
    )
    assert sentinel not in emitted


@pytest.mark.anyio
async def test_the_failure_sentinel_never_appears_in_the_streamed_output() -> None:
    stream_function = build_stub_model().stream_function
    assert stream_function is not None
    messages = _ask(f"shape my path please {FORCE_SHAPING_FAILURE}")

    seen: list[str] = []
    with pytest.raises(StubModelForcedError):
        async for item in stream_function(messages, _AGENT_INFO):
            assert isinstance(item, str)
            seen.append(item)

    assert FORCE_SHAPING_FAILURE not in "".join(seen)


def test_the_revision_instruction_never_appears_in_a_generated_passage() -> None:
    # The instruction is prompt material, not lesson prose: the marker is what
    # the regenerated passage carries.
    lesson = _lesson("Rust ownership", 3, revision=SHAPING_REVISION_INSTRUCTION)

    assert SHAPING_REVISION_INSTRUCTION not in lesson.read_passage
