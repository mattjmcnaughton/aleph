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
import re
from functools import cache
from typing import TYPE_CHECKING

from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from aleph.agents.lesson import LessonContent, QuickCheck
from aleph.agents.outline import LessonOutline, PathOutline, Refusal, UnitOutline

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo
    from pydantic_ai.tools import ToolDefinition

# --- sentinels -----------------------------------------------------------------

FORCE_OUTLINE_FAILURE = "[force-outline-failure]"
FORCE_REFUSAL = "[force-refusal]"
_FORCE_LESSON_FAILURE_RE = re.compile(r"\[force-lesson-failure:(\d+)\]")
# `position_in_path=<N>` (also tolerates `:` / whitespace) — the AL-032 contract.
_POSITION_RE = re.compile(r"position_in_path\s*[=:]?\s*(\d+)", re.IGNORECASE)
# Every sentinel, stripped from the topic before it appears in generated text.
_SENTINEL_RE = re.compile(
    r"\[force-outline-failure\]|\[force-refusal\]|\[force-lesson-failure:\d+\]"
)


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
    """The user text with sentinels removed and whitespace collapsed."""
    return " ".join(_SENTINEL_RE.sub("", text).split())


def _read_position(text: str) -> int | None:
    """The ``position_in_path`` the lesson prompt is generating, if present."""
    match = _POSITION_RE.search(text)
    return int(match.group(1)) if match else None


_ADJECTIVES = ("core", "practical", "foundational", "applied", "advanced", "essential")
_ASPECTS = ("concepts", "patterns", "pitfalls", "mechanics", "trade-offs", "examples")


def _pick(seq: tuple[str, ...], seed: int) -> str:
    return seq[seed % len(seq)]


# The topic is interpolated ~14× into a Read passage; an unbounded topic would
# push the passage past §14's ~500-word cap (a 12-word topic → ~532 words). Cap
# the topic *as used in the passage* so the band holds for any topic length.
# 8 words keeps the worst case ≈ 476 words, comfortably inside 500.
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


def _build_read_passage(topic: str, position: int) -> str:
    """A deterministic Read passage (~200-500 words, §14 band) for a lesson.

    The topic is truncated (:func:`_passage_topic`) for interpolation so the
    word band holds regardless of how long the caller's topic is.
    """
    seed = _seed(f"{topic}|passage|{position}")
    topic = _passage_topic(topic)
    lead = (
        f"This is lesson {position} on {topic}. It builds directly on the earlier "
        f"lessons in the path, extending rather than repeating them."
    )
    body = [
        f"The {_pick(_ASPECTS, seed + i)} of {topic} reward careful, "
        f"{_pick(_ADJECTIVES, seed + i)} study, and this passage walks through "
        f"them one idea at a time so the reader can follow without prior context."
        for i in range(12)
    ]
    close = (
        f"By the end of lesson {position}, a learner should be able to explain the "
        f"{_pick(_ASPECTS, seed)} of {topic} in their own words and recognise them "
        f"in practice. The Quick check below confirms that understanding."
    )
    return " ".join([lead, *body, close])


def _build_lesson(topic: str, position: int) -> LessonContent:
    """A deterministic, schema-valid lesson for ``topic`` at ``position``."""
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
            explanation=(
                f"Option {correct_index + 1} matches the passage's treatment of "
                f"{topic}; the others distort or misattribute it."
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
        lesson = _build_lesson(topic, position)
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


@cache
def build_stub_model() -> FunctionModel:
    """The process-wide deterministic stub :class:`FunctionModel`.

    ``@cache`` makes it a singleton: ``resolve_model("stub")`` returns the same
    object every call (identity, like the OpenRouter models), without a mutable
    module global. Tests that need a fresh instance construct
    ``FunctionModel(_stub_respond)`` directly.
    """
    return FunctionModel(_stub_respond)
