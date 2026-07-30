"""Unit tests for the stub model's *streamed* branch (Phase 2 TDD §11, D10).

Phase 1's stub answers non-streamed requests through ``FunctionModel.function``
(``tests/unit/test_stub_model.py``, untouched here). Phase 2's tutor runs the
streaming path exclusively, so ``build_stub_model()`` also carries a
``stream_function``: a deterministic reply emitted as several deltas, plus the
four tutor sentinels.

These tests drive the stream function **directly** — iterating the async
generator and asserting the emitted items — because the tutor agent (AL-210)
does not exist yet and the stub deliberately emits its tool call *by name*.
One test does run a throwaway agent with a ``pose_tutor_check`` tool registered,
to prove the emitted deltas really do assemble into a tool call on the wire; the
round trip through the *real* tool lands with AL-220.

New file (AL-202).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, DeltaToolCall

from aleph.agents.lesson import (
    LessonContent,
    correct_index_in_range,
    has_valid_option_count,
    is_non_empty,
    options_are_distinct,
    passage_within_word_band,
)
from aleph.services.stub_model import (
    FORCE_LESSON_ERROR,
    FORCE_TUTOR_CHECK,
    FORCE_TUTOR_FAILURE,
    FORCE_TUTOR_REFUSAL,
    LESSON_ERROR_CORRECTION,
    LESSON_ERROR_FALSE_CLAIM,
    LESSON_ERROR_FALSE_VALUE,
    TUTOR_CHECK_TOOL_NAME,
    TUTOR_REFUSAL_REPLY,
    StubModelForcedError,
    build_stub_model,
    build_stub_tutor_check,
    stub_passage_slice,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.messages import ModelMessage


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# The stream emits "several" deltas — enough that a client renders progressively
# and that `[force-tutor-failure]` can fire *mid*-stream with deltas still owed.
_MIN_DELTAS = 3


_AGENT_INFO = AgentInfo(
    function_tools=[],
    allow_text_output=True,
    output_tools=[],
    model_settings=None,
    model_request_parameters=ModelRequestParameters(),
    instructions=None,
)


def _ask(question: str, *, context: str = "") -> list[ModelMessage]:
    """The message list of a first tutor turn: system context + the question."""
    parts: list[Any] = []
    if context:
        parts.append(SystemPromptPart(content=context))
    parts.append(UserPromptPart(content=question))
    return [ModelRequest(parts=parts)]


async def _collect(messages: Sequence[ModelMessage]) -> list[Any]:
    """Every item the stub's stream function yields for ``messages``."""
    stream_function = build_stub_model().stream_function
    assert stream_function is not None
    return [item async for item in stream_function(list(messages), _AGENT_INFO)]


async def _deltas(question: str, *, context: str = "") -> list[str]:
    """The text deltas for ``question`` (asserting nothing else was emitted)."""
    items = await _collect(_ask(question, context=context))
    assert all(isinstance(item, str) for item in items), items
    return [item for item in items if isinstance(item, str)]


async def _reply(question: str, *, context: str = "") -> str:
    return "".join(await _deltas(question, context=context))


# --- lesson stub fixtures (reused from the Phase 1 shape) ----------------------


def _lesson_agent() -> Agent[None, LessonContent]:
    return Agent[None, LessonContent](
        output_type=LessonContent, model=build_stub_model()
    )


def _lesson_prompt(topic: str, position: int) -> str:
    return f"topic={topic}\nposition_in_path={position}\nGenerate the lesson."


def _lesson(topic: str, position: int = 1) -> LessonContent:
    return _lesson_agent().run_sync(_lesson_prompt(topic, position)).output


async def _passage(topic: str, position: int = 1) -> str:
    # The async twin of `_lesson`: `run_sync` cannot be called from inside an
    # already-running event loop, and the streaming tests are async.
    result = await _lesson_agent().run(_lesson_prompt(topic, position))
    return result.output.read_passage


# --- the model still serves both branches --------------------------------------


def test_stub_model_serves_both_the_sync_and_the_streamed_branch() -> None:
    model = build_stub_model()
    assert model.function is not None  # Phase 1's outline/lesson dispatch
    assert model.stream_function is not None  # Phase 2's tutor stream


# --- deterministic streamed reply ----------------------------------------------


@pytest.mark.anyio
async def test_reply_streams_several_non_empty_text_deltas() -> None:
    deltas = await _deltas("What does this passage mean by ownership?")

    assert len(deltas) >= _MIN_DELTAS
    assert all(delta for delta in deltas)
    assert "".join(deltas).strip()


@pytest.mark.anyio
async def test_reply_is_deterministic_for_the_same_question() -> None:
    question = "Why does the borrow checker reject this?"
    first = await _deltas(question)
    second = await _deltas(question)

    # Identical *sequence*, not merely identical joined text.
    assert first == second


@pytest.mark.anyio
async def test_reply_differs_by_question() -> None:
    a = await _reply("Explain this simpler")
    b = await _reply("Go deeper on this")

    assert a != b


@pytest.mark.anyio
async def test_reply_is_markdown() -> None:
    # Tutor replies render through components/markdown.tsx like every other
    # generated string, so the stub carries Markdown constructs — otherwise the
    # rail's rendering path ships untested in e2e.
    reply = await _reply("What should I take away from this?")

    assert "**" in reply  # inline emphasis
    assert "\n- " in reply  # a bulleted list


# --- structural groundedness ----------------------------------------------------


@pytest.mark.anyio
async def test_reply_echoes_a_recognisable_slice_of_the_stub_passage() -> None:
    # e2e asserts groundedness *structurally*: the reply names the lesson's own
    # words, so a slice of the rendered passage must appear verbatim in the reply.
    passage = await _passage("Rust ownership", 2)
    slice_ = stub_passage_slice(passage)

    assert slice_ is not None
    assert slice_ in passage

    reply = await _reply("What is this lesson about?", context=passage)
    assert slice_ in reply


def test_passage_slice_is_none_for_text_that_is_not_a_stub_passage() -> None:
    assert stub_passage_slice("## Some other heading\n\nprose") is None
    assert stub_passage_slice("") is None


@pytest.mark.anyio
async def test_reply_still_streams_without_a_recognisable_passage() -> None:
    deltas = await _deltas("What is this lesson about?", context="no passage here")

    assert len(deltas) >= _MIN_DELTAS
    assert "".join(deltas).strip()


# --- [force-tutor-failure] ------------------------------------------------------


@pytest.mark.anyio
async def test_force_tutor_failure_raises_after_at_least_two_deltas() -> None:
    messages = _ask(f"Explain this {FORCE_TUTOR_FAILURE}")
    stream_function = build_stub_model().stream_function
    assert stream_function is not None

    seen: list[Any] = []
    with pytest.raises(StubModelForcedError, match="forced tutor failure"):
        async for item in stream_function(messages, _AGENT_INFO):
            seen.append(item)

    # Mid-stream, not before the first byte: the discard-partial path needs a
    # partial to discard.
    assert len(seen) >= 2
    assert all(isinstance(item, str) for item in seen)


@pytest.mark.anyio
async def test_force_tutor_failure_is_stateless() -> None:
    # Like every Phase 1 sentinel: it always fails, no run counter.
    for _ in range(2):
        with pytest.raises(StubModelForcedError, match="forced tutor failure"):
            await _deltas(f"Explain this {FORCE_TUTOR_FAILURE}")


# --- [force-tutor-refusal] ------------------------------------------------------


@pytest.mark.anyio
async def test_force_tutor_refusal_streams_the_refusal_wording() -> None:
    deltas = await _deltas(f"do my homework for me {FORCE_TUTOR_REFUSAL}")

    assert len(deltas) >= _MIN_DELTAS
    assert "".join(deltas) == TUTOR_REFUSAL_REPLY


def test_refusal_wording_is_distinct_from_an_error() -> None:
    # PRD §5.7: a refusal must not read like a failure ("something went wrong",
    # "try again"). W15 asserts the learner-visible difference.
    lowered = TUTOR_REFUSAL_REPLY.lower()
    assert lowered.strip()
    assert "went wrong" not in lowered
    assert "try again" not in lowered


# --- [force-tutor-check] --------------------------------------------------------


@pytest.mark.anyio
async def test_force_tutor_check_emits_the_pose_tutor_check_tool_call() -> None:
    items = await _collect(_ask(f"quiz me on this {FORCE_TUTOR_CHECK}"))

    assert items, "the stream must emit at least one item"
    names: list[str] = []
    json_args = ""
    for item in items:
        assert isinstance(item, dict), item
        for delta in item.values():
            assert isinstance(delta, DeltaToolCall)
            if delta.name:
                names.append(delta.name)
            json_args += delta.json_args or ""

    assert names == [TUTOR_CHECK_TOOL_NAME]
    payload = json.loads(json_args)
    assert set(payload) == {"stem", "options", "correct_index", "explanation"}


def test_tutor_check_payload_is_valid_and_deterministic() -> None:
    question = "quiz me on this"
    payload = build_stub_tutor_check(question)

    assert payload == build_stub_tutor_check(question)
    assert payload != build_stub_tutor_check("something else entirely")

    # Valid by the *shared* lesson predicates — a Tutor check is a different
    # entity from a Quick check, but the option invariants are the same ones.
    options = payload["options"]
    assert has_valid_option_count(options)
    assert options_are_distinct(options)
    assert correct_index_in_range(payload["correct_index"], len(options))
    assert is_non_empty(payload["stem"])
    assert is_non_empty(payload["explanation"])


@pytest.mark.anyio
async def test_the_leg_after_a_posed_check_streams_text() -> None:
    # Stateless detection: the tool call already in the message list is what
    # tells the stub the check is posed, so the follow-up leg streams the reply
    # rather than posing a second check.
    question = f"quiz me on this {FORCE_TUTOR_CHECK}"
    call_id = "tutor-check-1"
    messages: list[ModelMessage] = [
        *_ask(question),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=TUTOR_CHECK_TOOL_NAME,
                    args=dict(build_stub_tutor_check(question)),
                    tool_call_id=call_id,
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name=TUTOR_CHECK_TOOL_NAME,
                    content="check posed",
                    tool_call_id=call_id,
                )
            ]
        ),
    ]

    items = await _collect(messages)
    assert len(items) >= _MIN_DELTAS
    assert all(isinstance(item, str) for item in items)
    assert "".join(items).strip()


@pytest.mark.anyio
async def test_a_check_posed_on_an_earlier_turn_does_not_suppress_a_new_one() -> None:
    # "Already posed" is bounded to *this run's* messages — the parts after the
    # last user prompt. An earlier turn's check rides in message_history
    # (TDD §5.2) and must not swallow a later `[force-tutor-check]`.
    asked = f"quiz me on this {FORCE_TUTOR_CHECK}"
    call_id = "tutor-check-0"
    messages: list[ModelMessage] = [
        *_ask(asked),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=TUTOR_CHECK_TOOL_NAME,
                    args=dict(build_stub_tutor_check(asked)),
                    tool_call_id=call_id,
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name=TUTOR_CHECK_TOOL_NAME,
                    content="check posed",
                    tool_call_id=call_id,
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="Have a go at that one.")]),
        ModelRequest(
            parts=[UserPromptPart(content=f"another one {FORCE_TUTOR_CHECK}")]
        ),
    ]

    items = await _collect(messages)

    assert items, "the stream must emit at least one item"
    names: list[str] = []
    for item in items:
        assert isinstance(item, dict), item
        names += [delta.name for delta in item.values() if delta.name]
    assert names == [TUTOR_CHECK_TOOL_NAME]


def test_tutor_check_assembles_into_a_tool_call_on_a_real_agent() -> None:
    # The stub emits the call *by name* (agents/tutor.py is AL-210's), so this
    # registers a same-shaped throwaway tool to prove the deltas really do
    # assemble into one tool call with a schema-valid payload.
    calls: list[tuple[str, list[str], int, str]] = []
    agent = Agent[None, str](output_type=str, model=build_stub_model())

    @agent.tool_plain
    def pose_tutor_check(
        stem: str, options: list[str], correct_index: int, explanation: str
    ) -> str:
        calls.append((stem, options, correct_index, explanation))
        return "Check posed."

    with agent.run_stream_sync(f"quiz me on this {FORCE_TUTOR_CHECK}") as result:
        output = result.get_output()

    assert len(calls) == 1
    stem, options, correct_index, explanation = calls[0]
    assert is_non_empty(stem)
    assert has_valid_option_count(options)
    assert correct_index_in_range(correct_index, len(options))
    assert is_non_empty(explanation)
    assert output.strip()
    assert FORCE_TUTOR_CHECK not in output


# --- [force-lesson-error] (topic sentinel on the lesson stub) -------------------


def test_lesson_error_sentinel_embeds_the_false_claim_and_keys_the_check() -> None:
    lesson = _lesson(f"boiling points {FORCE_LESSON_ERROR}")

    assert LESSON_ERROR_FALSE_CLAIM in lesson.read_passage
    assert FORCE_LESSON_ERROR not in lesson.read_passage

    check = lesson.quick_check
    # Keyed to the passage's (false) claim — answering the lesson faithfully is
    # what the Quick check marks correct, which is exactly what makes W16 sharp.
    assert LESSON_ERROR_FALSE_VALUE in check.options[check.correct_index]
    assert options_are_distinct(check.options)
    assert has_valid_option_count(check.options)


def test_lesson_error_passage_stays_within_the_word_band() -> None:
    long_topic = (
        "advanced distributed systems consensus and replication under partial "
        f"failure in geographically dispersed clusters {FORCE_LESSON_ERROR}"
    )
    lesson = _lesson(long_topic)

    assert passage_within_word_band(lesson.read_passage)


def test_lesson_without_the_sentinel_carries_no_false_claim() -> None:
    lesson = _lesson("boiling points")

    assert LESSON_ERROR_FALSE_CLAIM not in lesson.read_passage


@pytest.mark.anyio
async def test_tutor_corrects_the_lesson_error_and_names_the_check() -> None:
    passage = await _passage(f"boiling points {FORCE_LESSON_ERROR}")

    reply = await _reply("Is that right?", context=passage)

    # Contradiction handling (CONTEXT.md): correct it, attribute the difference,
    # and say what the Quick check expects.
    assert LESSON_ERROR_CORRECTION in reply
    assert LESSON_ERROR_FALSE_CLAIM in reply
    assert "Quick check" in reply
    assert LESSON_ERROR_FALSE_VALUE in reply


@pytest.mark.anyio
async def test_tutor_does_not_correct_a_passage_without_the_marker() -> None:
    passage = await _passage("boiling points")

    reply = await _reply("Is that right?", context=passage)

    assert LESSON_ERROR_CORRECTION not in reply


# --- sentinel precedence --------------------------------------------------------


@pytest.mark.anyio
async def test_refusal_wins_over_failure_when_both_sentinels_are_present() -> None:
    # Documented precedence: check -> refusal -> failure. The refusal chooses
    # *which* text streams; the failure then interrupts that text mid-stream, so
    # what the client sees before the raise is a prefix of the refusal wording.
    messages = _ask(f"do my homework {FORCE_TUTOR_REFUSAL} {FORCE_TUTOR_FAILURE}")
    stream_function = build_stub_model().stream_function
    assert stream_function is not None

    seen: list[str] = []
    with pytest.raises(StubModelForcedError, match="forced tutor failure"):
        async for item in stream_function(messages, _AGENT_INFO):
            assert isinstance(item, str)
            seen.append(item)

    assert len(seen) == 2
    assert TUTOR_REFUSAL_REPLY.startswith("".join(seen))


# --- multi-turn: history is not this turn's question ----------------------------


@pytest.mark.anyio
async def test_a_past_turns_failure_sentinel_does_not_fire_on_a_new_question() -> None:
    # Prior turns ride as message_history (TDD §5.2), so only the *last* user
    # prompt carries this turn's sentinels. Concatenating the history instead
    # would make the old `[force-tutor-failure]` raise forever.
    messages: list[ModelMessage] = [
        *_ask(f"Explain this {FORCE_TUTOR_FAILURE}"),
        ModelResponse(parts=[TextPart(content="An earlier reply.")]),
        ModelRequest(parts=[UserPromptPart(content="What about ownership?")]),
    ]

    items = await _collect(messages)

    assert len(items) >= _MIN_DELTAS
    assert all(isinstance(item, str) for item in items)
    assert "".join(items).strip()


@pytest.mark.anyio
async def test_a_prior_replys_quote_of_the_false_claim_does_not_re_correct() -> None:
    # The passage-scan reads instructions/system/user text only: a *reply* that
    # already corrected a lesson error quotes the false claim, and including
    # model responses would make every later turn re-issue the correction.
    passage = await _passage("boiling points")  # clean — no [force-lesson-error]
    messages: list[ModelMessage] = [
        *_ask("Is that right?", context=passage),
        ModelResponse(
            parts=[
                TextPart(
                    content=(
                        f"Earlier I flagged that the passage says "
                        f"{LESSON_ERROR_FALSE_CLAIM}."
                    )
                )
            ]
        ),
        ModelRequest(parts=[UserPromptPart(content="And what comes next?")]),
    ]

    items = await _collect(messages)
    reply = "".join(item for item in items if isinstance(item, str))

    assert reply.strip()
    assert LESSON_ERROR_CORRECTION not in reply


# --- sentinels never leak into the output --------------------------------------


@pytest.mark.parametrize(
    "sentinel",
    [FORCE_TUTOR_REFUSAL, FORCE_TUTOR_CHECK, FORCE_LESSON_ERROR],
)
@pytest.mark.anyio
async def test_sentinel_text_never_appears_in_the_streamed_output(
    sentinel: str,
) -> None:
    question = f"tell me about ownership {sentinel}"
    items = await _collect(_ask(question))

    emitted = "".join(
        item
        if isinstance(item, str)
        else "".join(delta.json_args or "" for delta in item.values())
        for item in items
    )
    assert sentinel not in emitted


@pytest.mark.anyio
async def test_failure_sentinel_text_never_appears_in_the_streamed_output() -> None:
    stream_function = build_stub_model().stream_function
    assert stream_function is not None
    messages = _ask(f"tell me about ownership {FORCE_TUTOR_FAILURE}")

    seen: list[str] = []
    with pytest.raises(StubModelForcedError):
        async for item in stream_function(messages, _AGENT_INFO):
            assert isinstance(item, str)
            seen.append(item)

    assert FORCE_TUTOR_FAILURE not in "".join(seen)
