"""Deterministic stub model for CI/e2e (TDD §12, D9).

A pydantic-ai :class:`~pydantic_ai.models.function.FunctionModel` injected at the
model-resolution seam (``services/openrouter.py`` resolves the ``stub`` id to
it). It drives the *real* agents unchanged, producing schema-valid outline and
lesson outputs **deterministically from the topic string** — so the same topic
always yields the same path, and Playwright can drive a real server process whose
models happen to be the stub.

It distinguishes outline from lesson generation by the shape of the agent's
output tool(s): an outline agent's union (``PathOutline | Refusal``) registers a
tool carrying ``units`` (and one carrying ``message``); a lesson agent registers
one carrying ``read_passage``.

**Sentinel topics force branches** (making W7/W8 first-class, repeatable tests):

- ``[force-outline-failure]`` — the outline call raises (path → ``failed``).
- ``[force-refusal]``          — the outline returns the ``Refusal`` branch.
- ``[force-lesson-failure:N]`` — the lesson call raises **only** when generating
  ``position_in_path == N`` (lesson N → ``failed``).
- ``[force-lesson-error]`` — the generated passage embeds a canonical *false*
  claim and keys its Quick check to it (Phase 2, W16; see below).

**Phase 2 — the streamed branch (TDD §11, D10).** The tutor runs the streaming
path exclusively, so the same model also carries a ``stream_function``
(:func:`_stub_stream`). It emits a deterministic reply — seeded from the
question text with the same :func:`_seed` trick — as several text deltas, and
echoes a recognizable slice of the stub's own Read passage
(:func:`stub_passage_slice`) so e2e can assert groundedness *structurally*: the
reply names the lesson's own words. Because the two branches never overlap (the
sync branch answers outline/lesson generation, the streamed branch answers tutor
turns), the stream function does **no** output-schema dispatch.

Its sentinels live in the **question text** and are stripped from the output,
and are as stateless as Phase 1's — no run counters, they always fire:

- ``[force-tutor-failure]``  — raises after two deltas, mid-stream, with deltas
  still owed (exercises the discard-partial path).
- ``[force-tutor-refusal]``  — streams :data:`TUTOR_REFUSAL_REPLY` verbatim.
- ``[force-tutor-check]``    — emits a ``pose_tutor_check`` tool call carrying
  :func:`build_stub_tutor_check`'s deterministic payload. The call is emitted
  **by name** because ``agents/tutor.py`` (AL-210) is being built in parallel
  and does not exist yet — not because the import would break layering (a
  service may import agents; this module already imports
  ``aleph.agents.lesson``). The round trip through the real tool is AL-220's
  integration concern.
- ``[force-lesson-error]``   — a *topic* sentinel on the lesson branch above,
  observed by the stream branch: seeing :data:`LESSON_ERROR_FALSE_CLAIM` in the
  assembled prompt, the reply corrects the lesson, attributes the difference,
  and says what the Quick check expects (CONTEXT.md *contradiction handling*).

Contract with AL-032 (``position_in_path``): the lesson agent's prompt **must**
carry ``position_in_path=<N>`` (the total-order position, TDD §4) so the stub can
read the position it is generating. Two properties AL-032 must preserve:

- **Presence is mandatory, not optional.** When a lesson output schema is in
  play, a prompt with no parseable ``position_in_path=<N>`` raises
  :class:`StubModelForcedError` rather than silently defaulting — a silent
  default would map every lesson to position 1, so ``[force-lesson-failure:N]``
  would never fire for ``N != 1`` and continuity/prefetch bugs would hide.
- **First match wins, so it must be unique.** :func:`_read_position` takes the
  *first* ``position_in_path=<N>`` in the concatenated user text. If AL-032's
  prompt also serializes the outline (which names ``position_in_path`` per
  lesson), that serialization must not precede the authoritative value, or the
  stub will misparse. Decision for the handoff: keep the regex unanchored and
  make **uniqueness the prompt's contract** (documented here) rather than
  hard-anchoring to a line format the prompt author hasn't fixed yet.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import cache
from typing import TYPE_CHECKING, TypedDict

from pydantic_ai.messages import (
    ModelResponse,
    SystemPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from aleph.agents.lesson import LessonContent, QuickCheck
from aleph.agents.outline import LessonOutline, PathOutline, Refusal, UnitOutline
from aleph.agents.tutor import TUTOR_CHECK_TOOL_NAME as AGENT_TUTOR_CHECK_TOOL_NAME

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo, DeltaToolCalls
    from pydantic_ai.tools import ToolDefinition

# --- sentinels -----------------------------------------------------------------

FORCE_OUTLINE_FAILURE = "[force-outline-failure]"
FORCE_REFUSAL = "[force-refusal]"
_FORCE_LESSON_FAILURE_RE = re.compile(r"\[force-lesson-failure:(\d+)\]")
# Phase 2 (TDD §11, D10). The first three are *question*-text sentinels read by
# the streamed branch; the fourth is a *topic* sentinel on the lesson branch
# whose effect the streamed branch then observes in the passage.
FORCE_TUTOR_FAILURE = "[force-tutor-failure]"
FORCE_TUTOR_REFUSAL = "[force-tutor-refusal]"
FORCE_TUTOR_CHECK = "[force-tutor-check]"
FORCE_LESSON_ERROR = "[force-lesson-error]"
# `position_in_path=<N>` (also tolerates `:` / whitespace) — the AL-032 contract.
_POSITION_RE = re.compile(r"position_in_path\s*[=:]?\s*(\d+)", re.IGNORECASE)
# Every sentinel, stripped from the topic/question before it appears in
# generated text. Alternatives are literal, so ordering is irrelevant.
_SENTINEL_RE = re.compile(
    r"\[force-outline-failure\]"
    r"|\[force-refusal\]"
    r"|\[force-lesson-failure:\d+\]"
    r"|\[force-lesson-error\]"
    r"|\[force-tutor-failure\]"
    r"|\[force-tutor-refusal\]"
    r"|\[force-tutor-check\]"
)


# The `pose_tutor_check` tool's name, **imported from the agent that registers
# it** rather than restated here (AL-220). AL-202 shipped it as a string literal
# because `agents/tutor.py` was being built in parallel and did not exist yet;
# now that it does, the honest arrangement is one definition and an import —
# services may import agents, and a stub emitting a tool call the agent does not
# register is a silent, CI-green way for the whole check path to stop working.
# Re-exported under the same name because every existing consumer (and the e2e
# suite) reaches for it here.
TUTOR_CHECK_TOOL_NAME = AGENT_TUTOR_CHECK_TOOL_NAME

# The canonical, checkable factual error `[force-lesson-error]` plants in a
# generated Read passage, and the correction the tutor streams back (W16).
# Deliberately concrete and unambiguous: e2e asserts the correction by string
# match, so the claim must be wrong in a way no phrasing can rescue.
LESSON_ERROR_FALSE_VALUE = "50 degrees Celsius"
_LESSON_ERROR_TRUE_VALUE = "100 degrees Celsius"
LESSON_ERROR_FALSE_CLAIM = f"water boils at {LESSON_ERROR_FALSE_VALUE} at sea level"
LESSON_ERROR_CORRECTION = f"water boils at {_LESSON_ERROR_TRUE_VALUE} at sea level"


def force_lesson_failure(position: int) -> str:
    """The ``[force-lesson-failure:N]`` sentinel for ``position`` (test helper)."""
    return f"[force-lesson-failure:{position}]"


class StubModelForcedError(RuntimeError):
    """Raised by the stub when a ``force-*-failure`` sentinel fires.

    Propagates out of ``agent.run`` exactly as a real provider error would, so
    the orchestrator's failure handling (path/lesson → ``failed``) is exercised.
    """


# --- deterministic helpers -----------------------------------------------------


def _seed(text: str) -> int:
    """A stable (cross-process) integer seed from ``text``.

    ``hash()`` is salted per process; SHA-256 keeps the stub deterministic
    across the pytest and the Playwright/server processes alike.
    """
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


def _user_text(messages: Sequence[ModelMessage]) -> str:
    """Concatenate every user-prompt string in the conversation.

    Only ``str`` ``UserPromptPart.content`` is collected; a part carrying a
    non-str content sequence (e.g. multimodal blobs) is skipped. That is
    correct for this stub — the agents feed it text topics/prompts — but the
    drop is deliberate and silent, so a future multimodal prompt would need to
    revisit this.
    """
    texts: list[str] = []
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                texts.append(part.content)
    return "\n".join(texts)


def _clean_topic(text: str) -> str:
    """The user text with sentinels removed and whitespace collapsed.

    Serves the tutor question as well as the generation topic (Phase 2): one
    stripping rule, so no sentinel of either era can reach generated prose. The
    name is Phase 1's and is kept — it is imported by the integration suite.
    """
    return " ".join(_SENTINEL_RE.sub("", text).split())


def _read_position(text: str) -> int | None:
    """The ``position_in_path`` the lesson prompt is generating, if present."""
    match = _POSITION_RE.search(text)
    return int(match.group(1)) if match else None


_ADJECTIVES = ("core", "practical", "foundational", "applied", "advanced", "essential")
_ASPECTS = ("concepts", "patterns", "pitfalls", "mechanics", "trade-offs", "examples")


def _pick(seq: tuple[str, ...], seed: int) -> str:
    return seq[seed % len(seq)]


# The topic is interpolated ~12× into a Read passage; an unbounded topic would
# push the passage past §14's ~500-word cap (a 12-word topic → ~532 words). Cap
# the topic *as used in the passage* so the band holds for any topic length.
# 8 words keeps the worst case ≈ 445 words, comfortably inside 500 (and a
# one-word topic ≈ 361, comfortably above the 200 floor).
_PASSAGE_TOPIC_MAX_WORDS = 8


def _passage_topic(topic: str) -> str:
    """``topic`` truncated to the word budget the passage band can afford."""
    words = topic.split()
    if len(words) <= _PASSAGE_TOPIC_MAX_WORDS:
        return topic
    return " ".join(words[:_PASSAGE_TOPIC_MAX_WORDS])


# --- content builders ----------------------------------------------------------


def _build_outline(topic: str) -> PathOutline:
    """A deterministic, cap-respecting outline for ``topic`` (TDD §14).

    2-4 units of 3-4 lessons each (well inside ``MAX_UNITS``=6 /
    ``MAX_LESSONS_PER_PATH``=30); every lesson title is globally unique.
    """
    base = _seed(topic)
    unit_count = 2 + base % 3  # 2..4
    units: list[UnitOutline] = []
    lesson_no = 0
    for u in range(unit_count):
        useed = _seed(f"{topic}|unit|{u}")
        lesson_count = 3 + useed % 2  # 3..4
        lessons: list[LessonOutline] = []
        for _ in range(lesson_count):
            lesson_no += 1
            lseed = _seed(f"{topic}|lesson|{lesson_no}")
            # ``lesson_no`` makes the title globally unique across the path.
            lessons.append(
                LessonOutline(title=f"{topic} — {_pick(_ASPECTS, lseed)} ({lesson_no})")
            )
        units.append(
            UnitOutline(
                title=f"{_pick(_ADJECTIVES, useed).capitalize()} {topic} ({u + 1})",
                summary=(
                    f"Unit {u + 1} builds {_pick(_ADJECTIVES, useed)} understanding "
                    f"of {topic}, covering its {_pick(_ASPECTS, useed)}."
                ),
                lessons=lessons,
            )
        )
    return PathOutline(units=units)


# The `[force-lesson-error]` block. Fixed wording (no topic interpolation) so
# its word cost is constant, and it replaces one body paragraph rather than
# adding to the passage — the §14 word band has to hold with the sentinel on.
_LESSON_ERROR_SECTION = (
    "### The figure this lesson works from\n\n"
    f"Every worked example below assumes that **{LESSON_ERROR_FALSE_CLAIM}**, and "
    "the Quick check at the end of this lesson is keyed to that figure rather "
    "than to anything you may have read elsewhere."
)
# The Quick check `[force-lesson-error]` substitutes: keyed to the passage's
# false claim, so answering the lesson faithfully is what the check marks
# correct. That tension is the whole point of W16 — the tutor has to correct the
# lesson *and* still tell the learner what the check expects.
_LESSON_ERROR_OPTIONS = (
    LESSON_ERROR_FALSE_VALUE,
    "80 degrees Celsius",
    _LESSON_ERROR_TRUE_VALUE,
    "120 degrees Celsius",
)


def _build_read_passage(
    topic: str, position: int, *, lesson_error: bool = False
) -> str:
    """A deterministic Markdown Read passage (~200-500 words, §14 band).

    The passage is **GitHub-Flavored Markdown**, like the real agent's output
    (``agents/lesson.py``): headings, prose, a bulleted list, a fenced code block,
    a GFM table, a ```mermaid diagram, and a blockquote. That is deliberate — the
    stub is what CI's e2e suite renders, so the Markdown path through
    ``components/markdown.tsx`` (and the lazily-loaded mermaid renderer behind it)
    is exercised on every run rather than only against a live model. Keep at least
    one instance of each construct here; dropping one silently stops testing that
    branch of the renderer.

    The topic is truncated (:func:`_passage_topic`) for interpolation so the word
    band holds regardless of how long the caller's topic is. Word counting is the
    validator's whitespace split, which counts Markdown punctuation (``##``,
    ``-``, the fences, the table pipes) as words — the fixed scaffolding below is
    budgeted with that included.

    ``lesson_error`` plants :data:`LESSON_ERROR_FALSE_CLAIM` in place of one body
    paragraph (``[force-lesson-error]``, W16). Substituting rather than appending
    keeps the word band intact for any topic length.
    """
    seed = _seed(f"{topic}|passage|{position}")
    topic = _passage_topic(topic)
    aspect = _pick(_ASPECTS, seed)
    lead = (
        f"## Lesson {position}: the {aspect} of {topic}\n\n"
        f"This is lesson {position} on **{topic}**. It builds directly on the "
        f"earlier lessons in the path, extending rather than repeating them."
    )
    paragraphs = [
        f"The {_pick(_ASPECTS, seed + i)} of {topic} reward careful, "
        f"{_pick(_ADJECTIVES, seed + i)} study, and this passage walks through "
        f"them one idea at a time so the reader can follow without prior context."
        for i in range(5 if lesson_error else 6)
    ]
    if lesson_error:
        paragraphs.append(_LESSON_ERROR_SECTION)
    body = "\n\n".join(paragraphs)
    bullets = "### What to hold on to\n\n" + "\n".join(
        f"- The {_pick(_ASPECTS, seed + i)} of {topic} stay "
        f"{_pick(_ADJECTIVES, seed + i)} once the earlier lessons have landed."
        for i in range(3)
    )
    code = (
        "### In practice\n\n"
        "```python\n"
        f"def lesson_{position}(learner):\n"
        f'    """Apply the {aspect} covered above."""\n'
        f"    return learner.practise({aspect!r})\n"
        "```\n\n"
        f"Read `lesson_{position}` as a sketch, not a runnable API: it names the "
        f"one move this lesson asks a learner to make."
    )
    table = (
        "| Idea | Why it matters |\n"
        "| --- | --- |\n"
        f"| {_pick(_ASPECTS, seed + 1)} | Carries over into the next lesson. |\n"
        f"| {_pick(_ASPECTS, seed + 2)} | Explains the Quick check below. |"
    )
    # A deliberately minimal, always-valid flowchart. It exists so the e2e suite
    # renders a real diagram through ``components/mermaid.tsx`` in a real browser;
    # keep it to plain `flowchart TD` with quoted labels — the same conservative
    # syntax the prompt asks the model for — so it cannot start failing to parse.
    diagram = (
        "The three moves of this lesson, in order:\n\n"
        "```mermaid\n"
        "flowchart TD\n"
        '    A["Read the passage"] --> B["Answer the Quick check"]\n'
        '    B --> C["Mark the lesson complete"]\n'
        "```"
    )
    close = (
        f"> By the end of lesson {position}, a learner should be able to explain "
        f"the {aspect} of {topic} in their own words and recognise them in "
        f"practice.\n\n"
        "The Quick check below confirms that understanding."
    )
    return "\n\n".join([lead, body, bullets, code, table, diagram, close])


def _build_lesson(
    topic: str, position: int, *, lesson_error: bool = False
) -> LessonContent:
    """A deterministic, schema-valid lesson for ``topic`` at ``position``.

    Under ``lesson_error`` (``[force-lesson-error]``) the passage carries a
    canonical false claim and the Quick check is **keyed to it** — the check's
    correct option is the one the (wrong) passage supports, so W16's tutor
    correction has something to be in tension with.
    """
    if lesson_error:
        return LessonContent(
            read_passage=_build_read_passage(topic, position, lesson_error=True),
            quick_check=QuickCheck(
                stem=(
                    "According to this lesson, at what temperature does water "
                    "boil at sea level?"
                ),
                options=list(_LESSON_ERROR_OPTIONS),
                correct_index=_LESSON_ERROR_OPTIONS.index(LESSON_ERROR_FALSE_VALUE),
                explanation=(
                    f"This lesson's passage states that {LESSON_ERROR_FALSE_CLAIM}, "
                    f"so the Quick check expects **{LESSON_ERROR_FALSE_VALUE}**."
                ),
            ),
        )

    seed = _seed(f"{topic}|quickcheck|{position}")
    options = [
        f"A {_pick(_ADJECTIVES, seed)} account of {topic}",
        f"An unrelated claim about {_pick(_ASPECTS, seed + 1)}",
        f"A common misconception about {topic}",
        f"A partially-correct statement about {_pick(_ASPECTS, seed + 2)}",
    ]
    correct_index = seed % len(options)
    return LessonContent(
        read_passage=_build_read_passage(topic, position),
        quick_check=QuickCheck(
            stem=f"Which statement best captures lesson {position} on {topic}?",
            options=options,
            correct_index=correct_index,
            # Inline Markdown only, matching the contract the real prompt states
            # for an explanation (no block structure in the small callout).
            explanation=(
                f"Option **{correct_index + 1}** matches the passage's treatment "
                f"of {topic}; the others distort or misattribute it."
            ),
        ),
    )


# --- FunctionModel callback ----------------------------------------------------


def _tool_with(
    output_tools: Sequence[ToolDefinition], prop: str
) -> ToolDefinition | None:
    """The first output tool whose JSON schema declares ``prop``."""
    for tool in output_tools:
        properties = tool.parameters_json_schema.get("properties", {})
        if prop in properties:
            return tool
    return None


def _stub_respond(messages: Sequence[ModelMessage], info: AgentInfo) -> ModelResponse:
    """The deterministic FunctionModel callback (dispatches outline vs lesson)."""
    text = _user_text(messages)
    topic = _clean_topic(text) or "the topic"

    lesson_tool = _tool_with(info.output_tools, "read_passage")
    outline_tool = _tool_with(info.output_tools, "units")
    refusal_tool = _tool_with(info.output_tools, "message")

    # A real agent registers *either* the lesson schema *or* the outline union,
    # never both. If both appear the dispatch below would silently prefer the
    # lesson branch; raise instead so a schema mistake is loud, not hidden.
    if lesson_tool is not None and (
        outline_tool is not None or refusal_tool is not None
    ):
        raise StubModelForcedError(
            "ambiguous output schema: both a lesson tool (read_passage) and an "
            "outline/refusal tool are present; the stub cannot choose a branch "
            f"(tools: {[tool.name for tool in info.output_tools]})"
        )

    if lesson_tool is not None:
        # Presence is mandatory (see module docstring): no silent default to 1.
        position = _read_position(text)
        if position is None:
            raise StubModelForcedError(
                "lesson prompt is missing a parseable 'position_in_path=<N>' "
                "(AL-032 contract); the stub cannot determine which lesson it is "
                "generating, so [force-lesson-failure:N] could not be honoured"
            )
        failure = _FORCE_LESSON_FAILURE_RE.search(text)
        if failure is not None and int(failure.group(1)) == position:
            raise StubModelForcedError(
                f"forced lesson failure at position_in_path={position}"
            )
        lesson = _build_lesson(topic, position, lesson_error=FORCE_LESSON_ERROR in text)
        return ModelResponse(
            parts=[ToolCallPart(tool_name=lesson_tool.name, args=lesson.model_dump())]
        )

    if outline_tool is not None or refusal_tool is not None:
        if FORCE_OUTLINE_FAILURE in text:
            raise StubModelForcedError("forced outline failure")
        if FORCE_REFUSAL in text and refusal_tool is not None:
            refusal = Refusal(
                message=(
                    "This topic falls outside what this tutor can responsibly teach, "
                    "so no learning path was created. Try a different subject."
                )
            )
            return ModelResponse(
                parts=[
                    ToolCallPart(tool_name=refusal_tool.name, args=refusal.model_dump())
                ]
            )
        if outline_tool is not None:
            outline = _build_outline(topic)
            return ModelResponse(
                parts=[
                    ToolCallPart(tool_name=outline_tool.name, args=outline.model_dump())
                ]
            )

    raise StubModelForcedError(
        "stub model could not recognise the agent's output schema "
        f"(tools: {[tool.name for tool in info.output_tools]})"
    )


# --- the streamed (tutor) branch — Phase 2, TDD §11 (D10) ----------------------

# The stub's own Read-passage lead heading (see :func:`_build_read_passage`).
# Matching *this* shape — rather than "the first heading in the prompt" — keeps
# the slice unambiguous: the tutor's system prompt (AL-210) is full of headings
# of its own, and only the passage's belongs to the lesson.
_PASSAGE_LEAD_RE = re.compile(r"^## (Lesson \d+: the [^\n]+)$", re.MULTILINE)

# "Several" deltas: enough for progressive rendering to be visible, and enough
# that `[force-tutor-failure]` fires with deltas still owed.
_TUTOR_DELTA_COUNT = 6
_FAILURE_AFTER_DELTAS = 2

_TUTOR_OPENERS = (
    "Good question.",
    "Happy to unpack that.",
    "Let's take that a step at a time.",
    "That one is worth pulling apart.",
    "Here is how this lesson handles it.",
    "Let's stay with the passage on this.",
)

# W15's assertion target: a refusal must read as a *boundary*, not a failure —
# no "something went wrong", no "try again" (PRD §5.7). Exported so the e2e and
# integration suites assert the exact wording rather than re-describing it.
TUTOR_REFUSAL_REPLY = (
    "I am not going to help with that one — it sits outside what this tutor "
    "covers, and I would rather say so plainly than answer it badly. Nothing "
    "has broken here: this is a boundary, not a failure. I am still right here "
    "for the lesson you are reading, so ask me about the passage and we can "
    "pick straight back up."
)

# Contradiction handling (CONTEXT.md; PRD §5.7b): correct it, attribute the
# difference plainly, and say what the Quick check expects.
_LESSON_ERROR_CORRECTION_BLOCK = (
    f"One flag before we go on: this lesson's passage says that "
    f"{LESSON_ERROR_FALSE_CLAIM}, and that is not right — {LESSON_ERROR_CORRECTION}. "
    f"I am naming the difference rather than quietly working around it, because "
    f"the Quick check on this lesson is keyed to the passage: it expects "
    f"**{LESSON_ERROR_FALSE_VALUE}**."
)


class TutorCheckPayload(TypedDict):
    """The ``pose_tutor_check`` arguments the stub emits (AL-210's tool shape)."""

    stem: str
    options: list[str]
    correct_index: int
    explanation: str


def stub_passage_slice(text: str) -> str | None:
    """The recognizable slice of a stub Read passage inside ``text``, if any.

    The lead heading of :func:`_build_read_passage`, without its ``##`` marker —
    so the returned string appears verbatim both in the passage source and in
    the passage as rendered. That is what makes e2e's groundedness assertion
    *structural*: the reply quotes it, the rendered lesson contains it.

    Returns ``None`` for anything that is not a stub passage (a real
    provider-generated lesson, or a prompt with no passage in it at all), and
    the reply then simply carries no quote — the stub never invents one.
    """
    match = _PASSAGE_LEAD_RE.search(text)
    return match.group(1) if match else None


def build_stub_tutor_check(question: str) -> TutorCheckPayload:
    """A deterministic, valid ``pose_tutor_check`` payload for ``question``.

    Valid by the same option invariants a Quick check obeys (3-4 distinct
    options, in-range ``correct_index``, non-empty stem/explanation) — a Tutor
    check is a *different entity*, but the shape is the same one. Public so
    tests, AL-220, and the e2e suite can assert the exact payload.
    """
    seed = _seed(f"{question}|tutor-check")
    options = [
        f"Because the passage's {_pick(_ASPECTS, seed)} say so",
        f"Because of an unrelated claim about {_pick(_ASPECTS, seed + 1)}",
        f"Because of a common misconception about {_pick(_ASPECTS, seed + 2)}",
        f"Because of a partially-correct reading of {_pick(_ASPECTS, seed + 3)}",
    ]
    correct_index = seed % len(options)
    return TutorCheckPayload(
        stem=(
            "Here is one back at you: which reading best matches what the "
            f"passage says about its {_pick(_ASPECTS, seed)}?"
        ),
        options=options,
        correct_index=correct_index,
        explanation=(
            f"Option **{correct_index + 1}** is the one the passage supports; "
            "the others drift away from it."
        ),
    )


def _last_user_text(messages: Sequence[ModelMessage]) -> str:
    """The most recent user-prompt string — the learner's question this turn.

    Deliberately *not* :func:`_user_text`: prior turns ride as ``message_history``
    (TDD §5.2), so concatenating them would make an old question's sentinel fire
    forever. The last user prompt is the question, and the sentinels are its.

    A tool round trip (``[force-tutor-check]``) appends no user prompt, so the
    follow-up leg still reads the same question — which is what keeps the
    sentinel behavior stateless across both legs.
    """
    for message in reversed(messages):
        for part in reversed(getattr(message, "parts", [])):
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                return part.content
    return ""


def _prompt_text(messages: Sequence[ModelMessage], info: AgentInfo) -> str:
    """Every instruction/system/user string in the request, concatenated.

    This is where the assembled lesson context lives, wherever AL-210/AL-211
    choose to put it (dynamic system prompt or user prompt) — so the passage
    scan below does not depend on that choice. Model *responses* are excluded on
    purpose: a prior reply that already corrected a lesson error would otherwise
    re-trigger the correction by quoting it.
    """
    texts: list[str] = []
    if info.instructions:
        texts.append(info.instructions)
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, SystemPromptPart | UserPromptPart) and isinstance(
                part.content, str
            ):
                texts.append(part.content)
    return "\n".join(texts)


def _tutor_check_posed(messages: Sequence[ModelMessage]) -> bool:
    """True when a ``pose_tutor_check`` call is already in this run's messages.

    Statelessness without a counter: the *conversation* records that the check
    was posed, so the leg after the tool return streams the reply instead of
    posing a second check.

    Bounded to the parts *after* the last :class:`UserPromptPart` — literally
    this run's messages. A check posed on an *earlier* turn rides in
    ``message_history`` (TDD §5.2), and must neither suppress a later
    ``[force-tutor-check]`` nor inject the "check just above" line into a reply
    that posed nothing.
    """
    parts = [part for message in messages for part in getattr(message, "parts", [])]
    asked = max(
        (i for i, part in enumerate(parts) if isinstance(part, UserPromptPart)),
        default=-1,
    )
    return any(
        isinstance(part, ToolCallPart | ToolReturnPart)
        and part.tool_name == TUTOR_CHECK_TOOL_NAME
        for part in parts[asked + 1 :]
    )


def _split_deltas(text: str) -> list[str]:
    """``text`` split into :data:`_TUTOR_DELTA_COUNT` chunks at word boundaries.

    Concatenating the result reproduces ``text`` exactly (whitespace and all),
    so a client that accumulates deltas ends up with the reply verbatim —
    exactly, that is, for text that does not *start* with whitespace, which the
    tokenizer would drop. Every reply this stub builds satisfies that.
    """
    tokens = re.findall(r"\S+\s*", text)
    if not tokens:
        return [text]
    count = min(_TUTOR_DELTA_COUNT, len(tokens))
    base, extra = divmod(len(tokens), count)
    chunks: list[str] = []
    start = 0
    for index in range(count):
        size = base + (1 if index < extra else 0)
        chunks.append("".join(tokens[start : start + size]))
        start += size
    return chunks


def _build_tutor_reply(
    question: str,
    *,
    passage_slice: str | None,
    lesson_error: bool,
    check_posed: bool,
) -> str:
    """A deterministic Markdown reply to ``question`` (Phase 1's ``_seed`` trick).

    Markdown for the same reason the Read passage is (``_build_read_passage``):
    the reply renders through ``components/markdown.tsx``, so e2e should be
    rendering real Markdown on every run.
    """
    seed = _seed(question)
    blocks = [
        f"{_pick(_TUTOR_OPENERS, seed)} You asked: “{question}”."
        if question
        else _pick(_TUTOR_OPENERS, seed)
    ]
    if passage_slice is not None:
        blocks.append(
            f"The passage you are reading opens with “{passage_slice}”, and this "
            "answer stays inside it rather than reaching for outside material."
        )
    if lesson_error:
        blocks.append(_LESSON_ERROR_CORRECTION_BLOCK)
    blocks.append(
        "Two things to hold on to:\n\n"
        f"- The **{_pick(_ASPECTS, seed)}** the passage describes are what your "
        "question turns on.\n"
        f"- The {_pick(_ADJECTIVES, seed)} reading is the one the passage itself "
        "gives, not one imported from elsewhere."
    )
    if check_posed:
        blocks.append("I have put a check to you just above — have a go at it.")
    blocks.append("Ask a follow-up if any part of that is still unclear.")
    return "\n\n".join(blocks)


async def _stub_stream(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str | DeltaToolCalls]:
    """The deterministic streamed callback — the tutor branch (TDD §11, D10).

    No output-schema dispatch: only the tutor streams, so the shape checks
    :func:`_stub_respond` performs would have nothing to choose between.

    Sentinel precedence, in the order applied below: ``[force-tutor-check]``
    takes the tool-call leg (nothing else can run on it — there is no text);
    then ``[force-tutor-refusal]`` chooses the text; then
    ``[force-tutor-failure]`` interrupts whichever text is being streamed. So
    combining check + failure fails the leg *after* the check, which is the only
    reading that keeps each sentinel's meaning intact.
    """
    asked = _last_user_text(messages)
    question = _clean_topic(asked)
    check_posed = _tutor_check_posed(messages)

    if FORCE_TUTOR_CHECK in asked and not check_posed:
        # Emitted as two deltas so the client's tool-argument accumulation is
        # exercised, not just a single whole-payload part.
        args = json.dumps(build_stub_tutor_check(question))
        split = len(args) // 2
        yield {0: DeltaToolCall(name=TUTOR_CHECK_TOOL_NAME, json_args=args[:split])}
        yield {0: DeltaToolCall(json_args=args[split:])}
        return

    if FORCE_TUTOR_REFUSAL in asked:
        reply = TUTOR_REFUSAL_REPLY
    else:
        prompt = _prompt_text(messages, info)
        reply = _build_tutor_reply(
            question,
            passage_slice=stub_passage_slice(prompt),
            lesson_error=LESSON_ERROR_FALSE_CLAIM in prompt,
            check_posed=check_posed,
        )

    fail_after = _FAILURE_AFTER_DELTAS if FORCE_TUTOR_FAILURE in asked else None
    for index, delta in enumerate(_split_deltas(reply)):
        if index == fail_after:
            raise StubModelForcedError(
                f"forced tutor failure after {index} deltas (mid-stream)"
            )
        yield delta


@cache
def build_stub_model() -> FunctionModel:
    """The process-wide deterministic stub :class:`FunctionModel`.

    ``@cache`` makes it a singleton: ``resolve_model("stub")`` returns the same
    object every call (identity, like the OpenRouter models), without a mutable
    module global. Tests that need a fresh instance construct
    ``FunctionModel(_stub_respond, stream_function=_stub_stream)`` directly.

    Both branches are bound: generation (outline/lesson) runs non-streamed
    through ``function``, the tutor runs streamed through ``stream_function``.
    """
    return FunctionModel(_stub_respond, stream_function=_stub_stream)
