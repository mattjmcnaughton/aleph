"""Unit tests for the assembled flashcard agent (Phase 3 TDD §5.2/§10/§11).

No network and no database: the agent binds no model, so every test injects a
``FunctionModel`` (or the deterministic stub, itself a ``FunctionModel``) at run
time and supplies the run inputs through ``FlashcardDeps``. Mirrors
``test_lesson_agent``'s shape (responder with capture + reuse-last,
retries-exhausted, retry-message-reaches-model, deps validation). Layers
exercised:

- the shared validator predicates (``count_within_band``, ``is_non_empty``,
  ``within_word_cap``, ``sides_differ``, ``restates_stem``) — the same functions
  the eval harness's layer-1 pre-filters import (TDD §10);
- prompt assembly (``build_flashcard_prompt``) — the ``flashcard_drafts=<N>``
  marker is first and unique, the Read passage is verbatim, and the Quick-check
  stem is carried without its options/explanation;
- the real assembled agent — a valid set of drafts passes, each PRD §6
  violation forces a ``ModelRetry``, and the retry budget bounds a persistently
  bad model;
- the stub driving the real agent (the TDD §12 CI/e2e contract), including the
  ``[force-draft-failure]`` sentinel and the mandatory ``flashcard_drafts=<N>``
  marker.
"""

from __future__ import annotations

import dataclasses
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

from aleph.agents.flashcard import (
    FlashcardCaps,
    FlashcardDeps,
    FlashcardDraft,
    FlashcardDrafts,
    build_flashcard_agent,
    build_flashcard_prompt,
    count_within_band,
    is_non_empty,
    restates_stem,
    sides_differ,
    validate_flashcard_drafts,
    within_word_cap,
)
from aleph.services.stub_model import (
    FORCE_DRAFT_FAILURE,
    StubModelForcedError,
    build_stub_model,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo
    from pydantic_ai.tools import ToolDefinition


# --- helpers -------------------------------------------------------------------

_STEM = "Which statement best captures how ownership works in Rust?"


def _valid_cards(count: int = 4) -> list[dict[str, str]]:
    return [
        {
            "front": f"Name one distinct fact from this lesson ({i}).",
            "back": f"A short, self-contained answer to fact {i}, standing alone.",
        }
        for i in range(count)
    ]


def _cards_with(index: int, **overrides: str) -> list[dict[str, str]]:
    cards = _valid_cards(4)
    cards[index] = {**cards[index], **overrides}
    return cards


def _drafts_dict(cards: list[dict[str, str]] | None = None) -> dict[str, object]:
    return {"cards": cards if cards is not None else _valid_cards()}


def _words(count: int) -> str:
    return " ".join(f"w{i}" for i in range(count))


def _deps(
    *,
    level: str = "beginner",
    topic: str = "Rust ownership",
    unit_title: str = "Foundations",
    lesson_title: str = "Ownership basics",
    read_passage: str = "Rust tracks ownership of every value at compile time. " * 10,
    quick_check_stem: str = _STEM,
    caps: FlashcardCaps | None = None,
) -> FlashcardDeps:
    # ``level`` is a plain str (some callers loop over it); every caller passes a
    # valid one and ``FlashcardDeps.__post_init__`` enforces the Level set.
    return FlashcardDeps(
        topic=topic,
        level=level,  # ty: ignore[invalid-argument-type]
        unit_title=unit_title,
        lesson_title=lesson_title,
        read_passage=read_passage,
        quick_check_stem=quick_check_stem,
        caps=caps or FlashcardCaps(),
    )


def _flashcard_tool(output_tools: Sequence[ToolDefinition]) -> ToolDefinition:
    """The flashcard agent's single output tool (the one carrying ``cards``)."""
    for tool in output_tools:
        if "cards" in tool.parameters_json_schema.get("properties", {}):
            return tool
    raise AssertionError("no output tool declares 'cards'")


class FlashcardResponder:
    """FunctionModel callback emitting one flashcard-drafts ``args`` dict per call.

    ``call_count`` lets a test assert a retry happened; ``messages_per_call``
    records what reached the model each call (system prompt, or a fed-back
    ``ModelRetry``). When ``responses`` is exhausted the last entry is reused, so
    a persistently-violating model drives past the retry budget without spelling
    out every identical response (mirrors ``test_lesson_agent``'s
    ``LessonResponder``).
    """

    __name__ = "flashcard_responder"

    def __init__(self, responses: list[dict[str, object]]) -> None:
        self._responses = responses
        self.call_count = 0
        self.messages_per_call: list[list[ModelMessage]] = []

    def __call__(
        self, messages: Sequence[ModelMessage], info: AgentInfo
    ) -> ModelResponse:
        self.messages_per_call.append(list(messages))
        args = self._responses[min(self.call_count, len(self._responses) - 1)]
        self.call_count += 1
        tool = _flashcard_tool(info.output_tools)
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


# --- shared validator predicates (exported for the eval harness, TDD §10) ------


def test_count_within_band() -> None:
    assert count_within_band(3, minimum=3, maximum=5)
    assert count_within_band(5, minimum=3, maximum=5)
    assert not count_within_band(2, minimum=3, maximum=5)
    assert not count_within_band(6, minimum=3, maximum=5)


def test_is_non_empty() -> None:
    assert is_non_empty("hello")
    assert not is_non_empty("   ")
    assert not is_non_empty("")


def test_within_word_cap() -> None:
    assert within_word_cap("one two three", maximum=3)
    assert not within_word_cap("one two three four", maximum=3)


def test_sides_differ() -> None:
    assert sides_differ("front text", "back text")
    # Case- and whitespace-insensitive, like the outline/lesson duplicate checks.
    assert not sides_differ("Same Text", " same text ")


def test_restates_stem_true_for_near_verbatim_restatement() -> None:
    assert restates_stem(_STEM, _STEM)


def test_restates_stem_true_for_a_light_rephrasing() -> None:
    front = "What statement best captures how Rust ownership works?"
    assert restates_stem(front, _STEM)


def test_restates_stem_false_for_an_unrelated_card() -> None:
    front = "What keyword marks a variable as mutable in Rust?"
    assert not restates_stem(front, _STEM)


def test_restates_stem_false_for_an_empty_stem() -> None:
    # Nothing to restate.
    assert not restates_stem("anything at all", "")


# --- regression: short/stopword-heavy stems must not false-positive ------------
#
# Verified false positives against the pre-fix shipped code (review finding
# #3): a short, stopword-heavy stem was trivially "covered" by an unrelated
# front sharing only its grammatical scaffolding, scoring 0.875 and 0.75
# respectively against the old 0.7 threshold — high enough that
# ``validate_flashcard_drafts`` would ``ModelRetry`` (and, with the retry
# budget exhausted, fail the whole drafting run) on a card that asks about
# something genuinely different from the Quick check.


def test_restates_stem_false_for_a_different_complexity_dimension() -> None:
    # Time complexity and space complexity are different facts about the same
    # algorithm — this is a different card, not a restatement, even though it
    # shares every function word with the stem.
    stem = "What is the time complexity of binary search?"
    front = "What is the space complexity of binary search?"
    assert not restates_stem(front, stem)


def test_restates_stem_false_for_a_short_stem_sharing_only_stopwords() -> None:
    # The stem's only content word ("closure") is a strict subset of the
    # front's own topic — not enough signal in a one-content-word stem to call
    # this a restatement rather than a legitimately deeper follow-up card.
    stem = "What is a closure?"
    front = "What is a closure's captured environment called?"
    assert not restates_stem(front, stem)


# --- known limitation: a light rephrasing of a short stem is NOT caught ---------
#
# Recorded as a limit, not a bug (review finding #4): the docstrings used to
# claim "genuine restatements and light rephrasings still land at 0.85-1.0",
# but that is not what the shipped 0.8 threshold delivers. A real Quick-check
# stem typically has 3-5 content words after stopword removal, and below five
# content tokens, changing even *one* content word already drops the overlap
# under 0.8 — so the honest tolerance at that length is **zero** changed
# words, not "one or two". This test pins one of the three verified
# counter-examples so the limitation stays documented and does not quietly
# regress into a claim of coverage the metric cannot back up: no re-tuning of
# this bag-of-content-words threshold separates this pair from the "time
# complexity" vs "space complexity" false positive the 0.7 -> 0.8 bump exists
# to kill (see `_RESTATEMENT_OVERLAP_THRESHOLD`'s comment for the full
# account) — that is the metric's honest ceiling.


def test_restates_stem_known_limitation_a_light_rephrasing_slips_through() -> None:
    stem = "Define tail recursion."
    front = "What is tail recursion?"
    # A human would call this the same question asked two different ways —
    # exactly the kind of restatement PRD §6 wants filtered — but it scores
    # 2/3 == 0.667 (only "tail"/"recursion" survive stopword removal from the
    # stem, and "define" does not appear in the front), under the 0.8 bar.
    assert not restates_stem(front, stem)


def test_flashcard_draft_and_drafts_are_plain_pydantic_models() -> None:
    draft = FlashcardDraft(front="F", back="B")
    drafts = FlashcardDrafts(cards=[draft])
    assert drafts.cards[0].front == "F"
    assert drafts.cards[0].back == "B"


# --- prompt assembly (build_flashcard_prompt) -----------------------------------


def test_prompt_carries_marker_first_and_exactly_once() -> None:
    prompt = build_flashcard_prompt(_deps())
    assert prompt.count("flashcard_drafts=") == 1
    assert prompt.startswith("flashcard_drafts=")


def test_prompt_marker_reflects_the_caps_band_midpoint() -> None:
    prompt = build_flashcard_prompt(_deps(caps=FlashcardCaps(count_min=3, count_max=5)))
    assert "flashcard_drafts=4" in prompt

    prompt_tight = build_flashcard_prompt(
        _deps(caps=FlashcardCaps(count_min=2, count_max=2))
    )
    assert "flashcard_drafts=2" in prompt_tight


def test_prompt_marker_stays_authoritative_even_if_the_topic_echoes_it() -> None:
    # The stub reads the FIRST `flashcard_drafts=<N>` in the request as
    # authoritative (services/stub_model.py contract, the position_in_path
    # precedent) — a topic string containing the same literal text must not
    # confuse a first-match parser.
    deps = _deps(topic="flashcard_drafts=999 is a slogan, not a real topic")
    prompt = build_flashcard_prompt(deps)
    first_section = prompt.split("\n\n", 1)[0]
    assert first_section == "flashcard_drafts=4"


def test_prompt_carries_topic_level_and_titles() -> None:
    prompt = build_flashcard_prompt(
        _deps(
            topic="Rust ownership",
            level="advanced",
            unit_title="Foundations",
            lesson_title="Ownership basics",
        )
    )
    assert "Rust ownership" in prompt
    assert "advanced" in prompt
    assert "Foundations" in prompt
    assert "Ownership basics" in prompt


def test_prompt_carries_the_read_passage_verbatim() -> None:
    passage = "A distinctive passage sentence nobody else would write. " * 5
    prompt = build_flashcard_prompt(_deps(read_passage=passage))
    assert passage in prompt


def test_prompt_carries_the_stem_and_instructs_not_to_restate_it() -> None:
    prompt = build_flashcard_prompt(_deps(quick_check_stem=_STEM))
    assert _STEM in prompt
    assert "restate" in prompt.lower()


def test_deps_carries_no_quick_check_options_or_explanation_field() -> None:
    # Structural, not prompt discipline: FlashcardDeps has nowhere to read the
    # Quick check's options or explanation from, so the prompt can never carry
    # them (TDD §5.2: "never the options or the explanation").
    field_names = {f.name for f in dataclasses.fields(FlashcardDeps)}
    assert "quick_check_options" not in field_names
    assert "quick_check_explanation" not in field_names
    assert "quick_check_stem" in field_names


# --- deps / caps construction (runtime validation, red-first) ------------------


def test_caps_rejects_an_inverted_count_band() -> None:
    with pytest.raises(ValueError, match="count_min"):
        FlashcardCaps(count_min=6, count_max=3)


def test_caps_accepts_equal_min_and_max() -> None:
    caps = FlashcardCaps(count_min=4, count_max=4)
    assert caps.count_min == caps.count_max == 4


def test_deps_rejects_unknown_level() -> None:
    # ``Level`` is a typing Literal (not runtime-enforced); __post_init__ rejects
    # a bad value at construction rather than as a bare KeyError in the prompt.
    with pytest.raises(ValueError, match="wizard"):
        FlashcardDeps(
            topic="t",
            level="wizard",  # ty: ignore[invalid-argument-type]
            unit_title="U",
            lesson_title="L",
            read_passage="p",
            quick_check_stem="s",
        )


def test_deps_accepts_each_valid_level() -> None:
    for level in ("beginner", "intermediate", "advanced"):
        assert _deps(level=level).level == level


# --- validate_flashcard_drafts (the composed layer-2 validator) ----------------


def test_validate_flashcard_drafts_accepts_a_valid_set() -> None:
    caps = FlashcardCaps()
    drafts = FlashcardDrafts.model_validate(_drafts_dict())
    result = validate_flashcard_drafts(caps, _STEM, drafts)
    assert result is drafts


@pytest.mark.parametrize(
    "cards,match",
    [
        pytest.param(_valid_cards(2), "between", id="too-few-cards"),
        pytest.param(_valid_cards(6), "between", id="too-many-cards"),
        pytest.param(_cards_with(0, front="   "), "front", id="empty-front"),
        pytest.param(_cards_with(0, back="   "), "back", id="empty-back"),
        pytest.param(_cards_with(0, front=_words(30)), "front", id="front-too-long"),
        pytest.param(_cards_with(0, back=_words(70)), "back", id="back-too-long"),
        pytest.param(
            _cards_with(0, front="Same text here", back="Same text here"),
            "repeats",
            id="sides-equal",
        ),
        pytest.param(_cards_with(0, front=_STEM), "restate", id="restates-stem"),
    ],
)
def test_validate_flashcard_drafts_rejects_each_violation(
    cards: list[dict[str, str]], match: str
) -> None:
    drafts = FlashcardDrafts.model_validate({"cards": cards})
    with pytest.raises(ModelRetry, match=match):
        validate_flashcard_drafts(FlashcardCaps(), _STEM, drafts)


# --- assembled agent: happy path + system prompt --------------------------------


def test_agent_returns_valid_drafts() -> None:
    agent = build_flashcard_agent()
    respond = FlashcardResponder([_drafts_dict()])
    deps = _deps()
    result = agent.run_sync(
        build_flashcard_prompt(deps), deps=deps, model=FunctionModel(respond)
    ).output
    assert isinstance(result, FlashcardDrafts)
    assert respond.call_count == 1


def test_agent_system_prompt_is_level_scoped() -> None:
    agent = build_flashcard_agent()
    respond = FlashcardResponder([_drafts_dict()])
    deps = _deps(level="advanced")
    agent.run_sync(
        build_flashcard_prompt(deps), deps=deps, model=FunctionModel(respond)
    )
    prompt = _system_prompt_text(respond.messages_per_call[0])
    assert "advanced" in prompt.lower()


def test_agent_system_prompt_carries_caps() -> None:
    # The count/word caps the validator enforces come from ``ctx.deps.caps``, so
    # the system prompt MUST target those same numbers — a static prompt
    # hardcoding the defaults would aim the model at one band while the
    # validator enforces another.
    agent = build_flashcard_agent()
    respond = FlashcardResponder([_drafts_dict()])
    caps = FlashcardCaps(
        count_min=2, count_max=6, front_words_max=10, back_words_max=20
    )
    deps = _deps(caps=caps)
    agent.run_sync(
        build_flashcard_prompt(deps), deps=deps, model=FunctionModel(respond)
    )
    prompt = _system_prompt_text(respond.messages_per_call[0])
    assert str(caps.count_min) in prompt
    assert str(caps.count_max) in prompt
    assert str(caps.front_words_max) in prompt
    assert str(caps.back_words_max) in prompt


def test_agent_system_prompt_differs_by_level() -> None:
    agent = build_flashcard_agent()
    prompts: dict[str, str] = {}
    for level in ("beginner", "intermediate", "advanced"):
        respond = FlashcardResponder([_drafts_dict()])
        deps = _deps(level=level)
        agent.run_sync(
            build_flashcard_prompt(deps), deps=deps, model=FunctionModel(respond)
        )
        prompts[level] = _system_prompt_text(respond.messages_per_call[0])
    assert len({prompts["beginner"], prompts["intermediate"], prompts["advanced"]}) == 3


# --- assembled agent: ModelRetry on each violation, then recovery --------------


@pytest.mark.parametrize(
    "invalid",
    [
        pytest.param(_drafts_dict(_valid_cards(2)), id="too-few-cards"),
        pytest.param(_drafts_dict(_valid_cards(6)), id="too-many-cards"),
        pytest.param(_drafts_dict(_cards_with(0, front="   ")), id="empty-front"),
        pytest.param(_drafts_dict(_cards_with(0, back="   ")), id="empty-back"),
        pytest.param(
            _drafts_dict(_cards_with(0, front=_words(30))), id="front-too-long"
        ),
        pytest.param(_drafts_dict(_cards_with(0, back=_words(70))), id="back-too-long"),
        pytest.param(
            _drafts_dict(_cards_with(0, front="Same text", back="Same text")),
            id="sides-equal",
        ),
        pytest.param(_drafts_dict(_cards_with(0, front=_STEM)), id="restates-stem"),
    ],
)
def test_agent_retries_on_each_violation_then_succeeds(
    invalid: dict[str, object],
) -> None:
    agent = build_flashcard_agent()
    deps = _deps()
    respond = FlashcardResponder([invalid, _drafts_dict()])
    result = agent.run_sync(
        build_flashcard_prompt(deps), deps=deps, model=FunctionModel(respond)
    ).output
    assert isinstance(result, FlashcardDrafts)
    assert respond.call_count == 2  # the ModelRetry forced a second call


def test_agent_feeds_validator_message_into_retry() -> None:
    # The validator's actionable message must reach the next call so the model
    # can self-correct (mirrors the lesson/outline agents' retry assertion).
    agent = build_flashcard_agent()
    deps = _deps()
    respond = FlashcardResponder(
        [_drafts_dict(_cards_with(0, front="   ")), _drafts_dict()]
    )
    result = agent.run_sync(
        build_flashcard_prompt(deps), deps=deps, model=FunctionModel(respond)
    ).output
    assert isinstance(result, FlashcardDrafts)
    assert respond.call_count == 2
    retry_text = _retry_prompt_text(respond.messages_per_call[1])
    assert "front" in retry_text.lower()


def test_agent_stops_after_retry_budget_when_model_never_complies() -> None:
    # A model that violates on every call must terminate: Agent(retries=3)
    # bounds output-validation retries, so 1 initial + 3 retries = 4 calls.
    agent = build_flashcard_agent()
    deps = _deps()
    respond = FlashcardResponder([_drafts_dict(_cards_with(0, front="   "))])
    with pytest.raises(UnexpectedModelBehavior):
        agent.run_sync(
            build_flashcard_prompt(deps), deps=deps, model=FunctionModel(respond)
        )
    assert respond.call_count == 4


# --- the stub drives the real agent (TDD §12) -----------------------------------


def test_stub_drafts_pass_the_real_validators() -> None:
    # The deterministic stub's drafts must satisfy the assembled agent's
    # validators unchanged (the CI/e2e contract, TDD §12): the run below only
    # succeeds if the agent's own output_validator accepted it, and we
    # re-assert explicitly.
    agent = build_flashcard_agent()
    deps = _deps(topic="US healthcare payment")
    result = agent.run_sync(
        build_flashcard_prompt(deps), deps=deps, model=build_stub_model()
    ).output
    assert isinstance(result, FlashcardDrafts)
    assert validate_flashcard_drafts(deps.caps, deps.quick_check_stem, result) is result


def test_stub_force_draft_failure_raises_through_the_real_agent() -> None:
    agent = build_flashcard_agent()
    deps = _deps(topic=f"Rust ownership {FORCE_DRAFT_FAILURE}")
    with pytest.raises(StubModelForcedError):
        agent.run_sync(
            build_flashcard_prompt(deps), deps=deps, model=build_stub_model()
        )
