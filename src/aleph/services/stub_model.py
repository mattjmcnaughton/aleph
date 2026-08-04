"""Deterministic stub model for CI/e2e (TDD §12, D9).

A pydantic-ai :class:`~pydantic_ai.models.function.FunctionModel` injected at the
model-resolution seam (``services/openrouter.py`` resolves the ``stub`` id to
it). It drives the *real* agents unchanged, producing schema-valid outline and
lesson outputs **deterministically from the topic string** — so the same topic
always yields the same path, and Playwright can drive a real server process whose
models happen to be the stub.

It distinguishes outline, lesson, and flashcard-drafting generation by the shape
of the agent's output tool(s): an outline agent's union (``PathOutline |
Refusal``) registers a tool carrying ``units`` (and one carrying ``message``); a
lesson agent registers one carrying ``read_passage``; a flashcard agent
(Phase 3) registers one carrying ``cards``.

**Sentinel topics force branches** (making W7/W8 first-class, repeatable tests):

- ``[force-outline-failure]`` — the outline call raises (path → ``failed``).
- ``[force-refusal]``          — the outline returns the ``Refusal`` branch.
- ``[force-lesson-failure:N]`` — the lesson call raises **only** when generating
  ``position_in_path == N`` (lesson N → ``failed``).
- ``[force-lesson-error]`` — the generated passage embeds a canonical *false*
  claim and keys its Quick check to it (Phase 2, W16; see below).
- ``[force-draft-failure]`` — the flashcard-drafting call raises (Phase 3; see
  below).

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

**Phase 2B — shaping (TDD §11, D12).** The shaping conversation runs the *same*
streamed branch as the tutor (a second agent, one stream function), so four more
question-text sentinels ride the same rules — stateless, stripped, always firing:

- ``[force-proposal-add]``     — emits a ``propose_path_edit`` tool call carrying
  :func:`build_stub_addition_proposal`'s deterministic 2-lesson **Addition** at
  ``first_shapeable_position``.
- ``[force-proposal-revise]``  — emits the same call carrying
  :func:`build_stub_revision_proposal`'s deterministic **Revision** of the first
  unengaged lesson, whose instruction is :data:`SHAPING_REVISION_INSTRUCTION`.
- ``[force-shaping-decline]``  — streams :data:`SHAPING_DECLINED_EDIT_REPLY`
  verbatim (the **declined edit**, W20's assertion target).
- ``[force-shaping-failure]``  — raises after two deltas, mid-stream, with deltas
  still owed (the discard-partial path, as ``[force-tutor-failure]``).

The proposal call's name is now **imported** from ``agents/shaper.py`` (AL-310),
which registers it — AL-302 emitted it by name while that module was being built
in parallel, exactly as AL-202 did for ``pose_tutor_check`` before AL-220
unwound it. One definition, imported.

Contract with AL-310/AL-311 (the shaping prompt): the deps block states the
engagement boundary **as data** (TDD §5.1), and the stub reads two markers out of
it:

- ``first_shapeable_position=<N>`` — §5.1's ``ShapingCaps.first_shapeable_position``,
  which that section already precomputes "so the prompt states the boundary as data".
- ``first_shapeable_lesson_id=<uuid>`` — the id of the lesson *at* that position.
  This one **extends** §5.1: its ``ShapingDigestEntry`` lists unit/lesson title,
  ``position_in_path``, unlock state, ``engaged`` and ``outcome``, but no id — yet a
  ``revise_lesson`` operation names its target *by id*, so the digest has to carry
  one for a Revision to be expressible at all. AL-310 added it
  (``agents/shaper.py``'s ``ShapingDigestEntry.lesson_id``, rendered per lesson
  and as this marker by ``render_shaping_context``).

Both markers must be rendered as **plain text lines** — ``name=value`` or
``name: value``, one per line — the shape :func:`_marker_re` builds and the same
one ``position_in_path`` uses below. The regexes additionally tolerate the
JSON-serialized form (``"name": "value"``) so a deps block rendered as JSON cannot
trip a marker that would then fail *loudly* for a reason that looks like a stub
bug; plain lines remain the contract, and are what the tests exercise.

Presence is mandatory for the matching sentinel: a missing marker raises
:class:`StubModelForcedError` rather than defaulting, exactly as for
``position_in_path`` below — a silent default would propose an edit *before* the
boundary and fail validation for reasons that look like agent error rather than a
missing contract.

Contract with AL-321 (revisions, D7): apply stores the proposal's instruction on
the lesson as ``revision_instruction`` and the Phase 1 lesson prompt carries it
in a revision block. When the stub's own :data:`SHAPING_REVISION_INSTRUCTION`
appears in a lesson prompt, the regenerated passage embeds
:data:`REVISED_PASSAGE_MARKER` — the structural link W18 asserts on (the
instruction reached generation), with no Phase 1 orchestration change.

**Phase 3 — flashcard drafting (TDD §5.2, §11).** The flashcard agent
(``agents/flashcard.py``) registers a single output tool carrying ``cards``, so
the dispatch below distinguishes it from the lesson/outline branches by that
tool shape, exactly as it already distinguishes lesson from outline. Its prompt
carries a ``flashcard_drafts=<N>`` marker — the AL-032 ``position_in_path``
precedent, restated for drafting: **presence is mandatory** (a missing marker
raises :class:`StubModelForcedError` rather than defaulting, since a silent
default could not honour a caller-chosen band) and **first match wins**, so the
marker must be unique in the request. A ``[force-draft-failure]`` *topic*
sentinel joins ``[force-outline-failure]``/``[force-lesson-failure:N]`` as a
third forced-failure sentinel, stripped from generated text like every other
sentinel here.

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

from aleph.agents.flashcard import FlashcardDraft, FlashcardDrafts
from aleph.agents.lesson import LessonContent, QuickCheck
from aleph.agents.outline import LessonOutline, PathOutline, Refusal, UnitOutline
from aleph.agents.shaper import (
    PROPOSE_PATH_EDIT_TOOL_NAME as AGENT_PROPOSE_PATH_EDIT_TOOL_NAME,
)
from aleph.agents.tutor import TUTOR_CHECK_TOOL_NAME as AGENT_TUTOR_CHECK_TOOL_NAME

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Sequence

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo, DeltaToolCalls
    from pydantic_ai.tools import ToolDefinition

# --- sentinels -----------------------------------------------------------------

FORCE_OUTLINE_FAILURE = "[force-outline-failure]"
FORCE_REFUSAL = "[force-refusal]"
_FORCE_LESSON_FAILURE_RE = re.compile(r"\[force-lesson-failure:(\d+)\]")
# Phase 3 (TDD §5.2, §11) — a topic sentinel on the flashcard-drafting branch,
# stateless and always firing, alongside the two forced-failure sentinels above.
FORCE_DRAFT_FAILURE = "[force-draft-failure]"
# Phase 2 (TDD §11, D10). The first three are *question*-text sentinels read by
# the streamed branch; the fourth is a *topic* sentinel on the lesson branch
# whose effect the streamed branch then observes in the passage.
FORCE_TUTOR_FAILURE = "[force-tutor-failure]"
FORCE_TUTOR_REFUSAL = "[force-tutor-refusal]"
FORCE_TUTOR_CHECK = "[force-tutor-check]"
FORCE_LESSON_ERROR = "[force-lesson-error]"
# Phase 2B (TDD §11, D12) — question-text sentinels on the same streamed branch,
# read on the shaping conversation.
FORCE_PROPOSAL_ADD = "[force-proposal-add]"
FORCE_PROPOSAL_REVISE = "[force-proposal-revise]"
FORCE_SHAPING_DECLINE = "[force-shaping-decline]"
FORCE_SHAPING_FAILURE = "[force-shaping-failure]"


def _marker_re(name: str, value: str) -> re.Pattern[str]:
    """A tolerant regex for a ``name=<value>`` prompt marker (see the docstring).

    The contract every caller documents is a plain text line — ``name=value`` or
    ``name: value``. The separator is optional and whitespace is free, and the
    JSON-serialized form ``"name": "value"`` matches too, so a prompt author who
    renders the deps block as JSON does not trip a mandatory marker. Matching is
    unanchored, so **first match wins and the marker must be unique** in the
    request (the AL-032 note below spells out why).
    """
    return re.compile(rf'{name}"?\s*[=:]?\s*"?({value})', re.IGNORECASE)


# `position_in_path=<N>` — the AL-032 contract.
_POSITION_RE = _marker_re("position_in_path", r"\d+")
# `flashcard_drafts=<N>` — the agents/flashcard.py contract (TDD §5.2).
_FLASHCARD_DRAFTS_RE = _marker_re("flashcard_drafts", r"\d+")
# The shaping prompt's statement of the engagement boundary — the AL-310/AL-311
# contract (see the module docstring).
_FIRST_SHAPEABLE_POSITION_RE = _marker_re("first_shapeable_position", r"\d+")
_FIRST_SHAPEABLE_LESSON_ID_RE = _marker_re(
    "first_shapeable_lesson_id",
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
)
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
    r"|\[force-proposal-add\]"
    r"|\[force-proposal-revise\]"
    r"|\[force-shaping-decline\]"
    r"|\[force-shaping-failure\]"
    r"|\[force-draft-failure\]"
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

# The `propose_path_edit` tool's name (TDD §5.1, D4), **imported from the agent
# that registers it** rather than restated here (AL-310). AL-302 shipped it as a
# string literal because `agents/shaper.py` was being built in parallel and did
# not exist yet, and asked for exactly this unwind once it landed: one
# definition, imported — a service may import an agent, and a stub emitting a
# tool call the agent does not register is a silent, CI-green way for the whole
# proposal path to stop working. Re-exported under the same name because every
# existing consumer (and the e2e suite) reaches for it here.
PROPOSE_PATH_EDIT_TOOL_NAME = AGENT_PROPOSE_PATH_EDIT_TOOL_NAME

# The instruction `[force-proposal-revise]` puts on its Revision, and the marker
# the regenerated passage then carries (W18). The instruction rides proposal →
# `revision_instruction` → the Phase 1 lesson prompt (D7); seeing *its own*
# instruction in a lesson prompt is how the stub knows it is regenerating a
# revised lesson, so the pair is a closed, exactly-checkable loop.
#
# What the loop *does* depend on: AL-321's revision block must carry these words
# verbatim. Layout is free — :func:`_revision_requested` collapses whitespace on
# both sides, so re-indenting or re-wrapping the block is safe — but paraphrasing,
# truncating, or interpolating into the instruction breaks W18 silently.
SHAPING_REVISION_INSTRUCTION = (
    "Re-pitch this lesson for someone meeting the idea for the first time: keep "
    "every factual commitment of the version it replaces, slow the explanation "
    "down, and work one concrete example all the way through."
)
REVISED_PASSAGE_MARKER = "This passage was regenerated from a learner's revision."

# The **declined edit** (CONTEXT.md; PRD §5.7): the graceful reply to an
# out-of-vocabulary ask. It names what shaping *can* do, does not apologize
# twice, and reads as neither a failure nor a safety refusal — W20 asserts this
# exact wording, so it is exported rather than re-described in the suites.
SHAPING_DECLINED_EDIT_REPLY = (
    "That is not one of the changes I can make to a path. What shaping can do is "
    "**add** lessons — on their own or grouped as a new unit — anywhere you have "
    "not started yet, and **revise** a lesson you have not started yet so it "
    "lands differently. What it cannot do is remove or reorder lessons, change "
    "work you have already engaged with, or touch your progress. Your path is "
    "exactly as it was. Tell me what you were hoping that change would get you "
    "and there is a good chance an addition or a revision gets you there."
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


def _collapse_ws(text: str) -> str:
    """``text`` with every run of whitespace collapsed to a single space."""
    return " ".join(text.split())


def clean_topic(text: str) -> str:
    """The user text with sentinels removed and whitespace collapsed.

    Serves the tutor question as well as the generation topic (Phase 2): one
    stripping rule, so no sentinel of either era can reach generated prose.

    **Public**, because it is part of this module's test-facing surface: three
    integration suites build their expected payloads with it, and a suite
    reaching for an underscored name is the module telling you the name is
    wrong. Spelled ``_clean_topic`` through Phase 2A; renamed, not aliased, so
    there is one spelling rather than two.
    """
    return _collapse_ws(_SENTINEL_RE.sub("", text))


def _revision_requested(prompt: str) -> bool:
    """True when ``prompt`` carries the stub's own revision instruction (D7, W18).

    Compared on whitespace-collapsed text at both ends: the instruction reaches
    the lesson prompt inside AL-321's revision block, and a template that
    re-indents or re-wraps its ~40 words must not silently break the W18 link.
    Word-for-word identity is still required — only whitespace is normalized.
    """
    return _collapse_ws(SHAPING_REVISION_INSTRUCTION) in _collapse_ws(prompt)


def _read_position(text: str) -> int | None:
    """The ``position_in_path`` the lesson prompt is generating, if present."""
    match = _POSITION_RE.search(text)
    return int(match.group(1)) if match else None


def _read_flashcard_drafts(text: str) -> int | None:
    """The ``flashcard_drafts=<N>`` count the drafting prompt states, if present."""
    match = _FLASHCARD_DRAFTS_RE.search(text)
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

    2-4 units of 3-4 lessons each (well inside ``MAX_UNITS``=25 /
    ``MAX_LESSONS_PER_PATH``=200); every lesson title is globally unique.
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

# The revision block (Phase 2B, D7). Fixed wording like the lesson-error block
# above, and substituted for a body paragraph for the same reason: the §14 word
# band has to hold with either marker on, or both.
_REVISION_SECTION = (
    "### What changed in this pass\n\n"
    f"{REVISED_PASSAGE_MARKER} It keeps every factual commitment of the version "
    "it replaces and changes only how the material is pitched, so the lessons "
    "around it still line up."
)


def _build_read_passage(
    topic: str, position: int, *, lesson_error: bool = False, revised: bool = False
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
    paragraph (``[force-lesson-error]``, W16); ``revised`` plants
    :data:`REVISED_PASSAGE_MARKER` in place of another (Phase 2B, D7/W18).
    Substituting rather than appending keeps the word band intact for any topic
    length and for any combination of the two.
    """
    seed = _seed(f"{topic}|passage|{position}")
    topic = _passage_topic(topic)
    aspect = _pick(_ASPECTS, seed)
    lead = (
        f"## Lesson {position}: the {aspect} of {topic}\n\n"
        f"This is lesson {position} on **{topic}**. It builds directly on the "
        f"earlier lessons in the path, extending rather than repeating them."
    )
    sections = [
        section
        for section, present in (
            (_LESSON_ERROR_SECTION, lesson_error),
            (_REVISION_SECTION, revised),
        )
        if present
    ]
    paragraphs = [
        f"The {_pick(_ASPECTS, seed + i)} of {topic} reward careful, "
        f"{_pick(_ADJECTIVES, seed + i)} study, and this passage walks through "
        f"them one idea at a time so the reader can follow without prior context."
        for i in range(6 - len(sections))
    ]
    body = "\n\n".join(paragraphs + sections)
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
    topic: str, position: int, *, lesson_error: bool = False, revised: bool = False
) -> LessonContent:
    """A deterministic, schema-valid lesson for ``topic`` at ``position``.

    Under ``lesson_error`` (``[force-lesson-error]``) the passage carries a
    canonical false claim and the Quick check is **keyed to it** — the check's
    correct option is the one the (wrong) passage supports, so W16's tutor
    correction has something to be in tension with.

    Under ``revised`` (the prompt carries :data:`SHAPING_REVISION_INSTRUCTION`,
    D7) the passage carries :data:`REVISED_PASSAGE_MARKER`. Only the passage
    changes: a Revision regenerates the lesson, and the Quick check is rebuilt
    from the same deterministic seed as any other generation of this slot.
    """
    if lesson_error:
        return LessonContent(
            read_passage=_build_read_passage(
                topic, position, lesson_error=True, revised=revised
            ),
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
        read_passage=_build_read_passage(topic, position, revised=revised),
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


# --- flashcard drafts (Phase 3, TDD §5.2) ---------------------------------------

# A card interpolates the topic once; a much smaller budget than the lesson
# passage's (``_PASSAGE_TOPIC_MAX_WORDS``, 8) keeps a front within
# ``FlashcardCaps``' default 25-word cap regardless of how long ``topic`` turns
# out to be — and, for the real assembled agent, ``topic`` here is the *whole*
# concatenated user prompt (``_stub_respond``'s ``clean_topic(text)``, same as
# the lesson branch), not just the ``Topic: …`` line's value, so it can be long.
_FLASHCARD_TOPIC_MAX_WORDS = 6


def _flashcard_topic(topic: str) -> str:
    """``topic`` truncated to the word budget a card's front can afford."""
    words = topic.split()
    if len(words) <= _FLASHCARD_TOPIC_MAX_WORDS:
        return topic
    return " ".join(words[:_FLASHCARD_TOPIC_MAX_WORDS])


def _build_flashcard_draft(topic: str, index: int) -> FlashcardDraft:
    """A deterministic, cap-respecting card for ``topic`` at ``index`` (0-based).

    Front/back stay well under ``FlashcardCaps``' default word caps for any
    reasonable topic, and are deliberately unlike the stub's own Quick-check
    stem wording (``"Which statement best captures lesson N on {topic}?"``) so
    a generated card does not spuriously trip :func:`restates_stem
    <aleph.agents.flashcard.restates_stem>` against the stub's own lesson.
    """
    seed = _seed(f"{topic}|flashcard|{index}")
    aspect = _pick(_ASPECTS, seed)
    adjective = _pick(_ADJECTIVES, seed + 1)
    short_topic = _flashcard_topic(topic)
    # ``index`` makes the front globally unique for a fixed topic — the same
    # discipline ``_build_outline``'s ``lesson_no`` uses — since the seeded
    # word picks alone can collide within a small band (6 adjectives, up to 5
    # cards).
    return FlashcardDraft(
        front=(
            f"Name one {adjective} thing to remember about {short_topic} ({index + 1})."
        ),
        back=(
            f"Its {aspect}: the passage's point a learner should still be able "
            f"to recall long after finishing this lesson."
        ),
    )


def _build_flashcard_drafts(topic: str, count: int) -> FlashcardDrafts:
    """``count`` deterministic, schema-valid cards for ``topic``.

    ``count`` is the ``flashcard_drafts=<N>`` prompt marker (:func:`
    _read_flashcard_drafts`), read exactly as ``position_in_path`` is read for
    the lesson branch. Each card is distinct (the index seeds it) and short
    enough to satisfy any non-degenerate :class:`FlashcardCaps
    <aleph.agents.flashcard.FlashcardCaps>` band.
    """
    return FlashcardDrafts(
        cards=[_build_flashcard_draft(topic, index) for index in range(count)]
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
    topic = clean_topic(text) or "the topic"

    lesson_tool = _tool_with(info.output_tools, "read_passage")
    outline_tool = _tool_with(info.output_tools, "units")
    refusal_tool = _tool_with(info.output_tools, "message")
    flashcard_tool = _tool_with(info.output_tools, "cards")

    # A real agent registers *either* the lesson schema, the outline union, or
    # the flashcard-drafts schema, never more than one. If more than one appears
    # the dispatch below would silently prefer one branch; raise instead so a
    # schema mistake is loud, not hidden.
    if lesson_tool is not None and (
        outline_tool is not None
        or refusal_tool is not None
        or flashcard_tool is not None
    ):
        raise StubModelForcedError(
            "ambiguous output schema: both a lesson tool (read_passage) and an "
            "outline/refusal/flashcard tool are present; the stub cannot choose "
            f"a branch (tools: {[tool.name for tool in info.output_tools]})"
        )
    if flashcard_tool is not None and (
        outline_tool is not None or refusal_tool is not None
    ):
        raise StubModelForcedError(
            "ambiguous output schema: both a flashcard-drafts tool (cards) and "
            "an outline/refusal tool are present; the stub cannot choose a "
            f"branch (tools: {[tool.name for tool in info.output_tools]})"
        )

    if flashcard_tool is not None:
        if FORCE_DRAFT_FAILURE in text:
            raise StubModelForcedError("forced flashcard draft failure")
        count = _read_flashcard_drafts(text)
        if count is None:
            raise StubModelForcedError(
                "flashcard prompt is missing a parseable 'flashcard_drafts=<N>' "
                "(the agents/flashcard.py contract); the stub cannot determine "
                "how many drafts to emit"
            )
        drafts = _build_flashcard_drafts(topic, count)
        return ModelResponse(
            parts=[
                ToolCallPart(tool_name=flashcard_tool.name, args=drafts.model_dump())
            ]
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
        # The revision block lands wherever AL-321's prompt puts it (system or
        # user text), so the scan is over the whole request — D7's instruction is
        # what tells the stub this is a Revision regenerating (W18).
        lesson = _build_lesson(
            topic,
            position,
            lesson_error=FORCE_LESSON_ERROR in text,
            revised=_revision_requested(_prompt_text(messages, info)),
        )
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


# --- the proposal payload (Phase 2B, TDD §4/§5.1) ------------------------------


class ProposedLesson(TypedDict):
    """One lesson an Addition inserts."""

    title: str


class ProposedUnit(TypedDict):
    """The new unit an Addition may group its lessons under."""

    title: str
    summary: str


class AddLessonsOperation(TypedDict):
    """The ``add_lessons`` operation shape (TDD §4)."""

    insert_at_position: int
    new_unit: ProposedUnit | None
    lessons: list[ProposedLesson]
    rationale: str
    estimated_minutes: int


class ReviseLessonOperation(TypedDict):
    """The ``revise_lesson`` operation shape (TDD §4)."""

    lesson_id: str
    instruction: str
    new_title: str | None
    rationale: str


class PathProposalPayload(TypedDict):
    """The ``propose_path_edit`` arguments the stub emits (AL-310's tool shape).

    Exactly TDD §4's fixed payload — ``{operations, summary}`` over a closed
    two-shape vocabulary (D1). The shapes are discriminated structurally (an
    Addition carries ``lessons``, a Revision carries ``lesson_id``), which is how
    the rest of this module dispatches too; if AL-310's schema ends up tagging
    them, adding a tag here is additive.
    """

    operations: list[AddLessonsOperation | ReviseLessonOperation]
    summary: str


# The sentinel Addition's size: "a couple of lessons", well under
# `MAX_LESSONS_PER_PROPOSAL` (5, TDD §13 — config's number, not the stub's; this
# module reads no config).
_ADDITION_LESSON_COUNT = 2
# Its cost line, in the Proposal card's "adds 2 lessons ≈ 10 min" shape.
_ADDITION_MINUTES_PER_LESSON = 5


def build_stub_addition_proposal(
    question: str, *, insert_at_position: int
) -> PathProposalPayload:
    """A deterministic, valid 2-lesson **Addition** at ``insert_at_position``.

    Public so the unit, integration, and e2e suites can assert the exact payload
    the ``[force-proposal-add]`` sentinel produces. Titles are distinct by
    construction and shaped unlike :func:`_build_outline`'s, so an added lesson
    is recognizable in a rail full of generated ones.
    """
    seed = _seed(f"{question}|proposal-add|{insert_at_position}")
    lessons = [
        ProposedLesson(
            title=(
                f"Added on request: the {_pick(_ASPECTS, seed + index)} "
                f"({index + 1} of {_ADDITION_LESSON_COUNT})"
            )
        )
        for index in range(_ADDITION_LESSON_COUNT)
    ]
    operations: list[AddLessonsOperation | ReviseLessonOperation] = [
        AddLessonsOperation(
            insert_at_position=insert_at_position,
            new_unit=None,
            lessons=lessons,
            rationale=(
                f"You asked for more on the {_pick(_ASPECTS, seed)} here, and the "
                f"path does not cover them yet."
            ),
            estimated_minutes=_ADDITION_LESSON_COUNT * _ADDITION_MINUTES_PER_LESSON,
        )
    ]
    return PathProposalPayload(
        operations=operations,
        summary=(
            f"Adds {_ADDITION_LESSON_COUNT} lessons at position "
            f"{insert_at_position}, about "
            f"{_ADDITION_LESSON_COUNT * _ADDITION_MINUTES_PER_LESSON} minutes. "
            "Nothing you have already worked through moves."
        ),
    )


def build_stub_revision_proposal(
    question: str, *, lesson_id: str
) -> PathProposalPayload:
    """A deterministic, valid **Revision** of the lesson ``lesson_id``.

    The instruction is :data:`SHAPING_REVISION_INSTRUCTION` verbatim — that is
    what makes the regenerated passage carry :data:`REVISED_PASSAGE_MARKER` once
    apply has written it to ``revision_instruction`` (D7, W18).
    """
    seed = _seed(f"{question}|proposal-revise|{lesson_id}")
    operations: list[AddLessonsOperation | ReviseLessonOperation] = [
        ReviseLessonOperation(
            lesson_id=lesson_id,
            instruction=SHAPING_REVISION_INSTRUCTION,
            new_title=f"Revised on request: the {_pick(_ASPECTS, seed)} of this lesson",
            rationale=(
                "You have not started this lesson yet, so it can be re-pitched "
                "in place rather than added around."
            ),
        )
    ]
    return PathProposalPayload(
        operations=operations,
        summary=(
            "Revises one lesson you have not started yet, keeping its place in "
            "the path. Nothing is added, removed, or reordered."
        ),
    )


def _read_first_shapeable_position(prompt: str) -> int:
    """The engagement boundary the shaping prompt states (AL-310/AL-311 contract).

    Mandatory, never defaulted: see the module docstring. Without it the stub
    cannot say where an Addition may legally land.
    """
    match = _FIRST_SHAPEABLE_POSITION_RE.search(prompt)
    if match is None:
        raise StubModelForcedError(
            "shaping prompt is missing a parseable 'first_shapeable_position=<N>' "
            "(the AL-310/AL-311 contract); the stub cannot place a valid Addition "
            "at the engagement boundary, so [force-proposal-add] could not be "
            "honoured"
        )
    return int(match.group(1))


def _read_first_shapeable_lesson_id(prompt: str) -> str:
    """The id of the first unengaged lesson — the Revision target (same contract)."""
    match = _FIRST_SHAPEABLE_LESSON_ID_RE.search(prompt)
    if match is None:
        raise StubModelForcedError(
            "shaping prompt is missing a parseable "
            "'first_shapeable_lesson_id=<uuid>' (the AL-310/AL-311 contract); the "
            "stub cannot name an unengaged Revision target, so "
            "[force-proposal-revise] could not be honoured"
        )
    return match.group(1)


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


def _tool_called_this_run(messages: Sequence[ModelMessage], tool_name: str) -> bool:
    """True when ``tool_name`` was already called in this run's messages.

    Statelessness without a counter: the *conversation* records that the check
    was posed (or the proposal made), so the leg after the tool return streams
    the reply instead of calling the tool a second time.

    Bounded to the parts *after* the last :class:`UserPromptPart` — literally
    this run's messages. A check posed on an *earlier* turn rides in
    ``message_history`` (TDD §5.2), and must neither suppress a later
    ``[force-tutor-check]`` nor inject the "check just above" line into a reply
    that posed nothing. The same holds for ``propose_path_edit`` (Phase 2B).
    """
    parts = [part for message in messages for part in getattr(message, "parts", [])]
    asked = max(
        (i for i, part in enumerate(parts) if isinstance(part, UserPromptPart)),
        default=-1,
    )
    return any(
        isinstance(part, ToolCallPart | ToolReturnPart) and part.tool_name == tool_name
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
    proposal_made: bool,
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
    if proposal_made:
        # The Proposal is a card, not prose: the reply points at it and stops
        # short of claiming anything has changed. Only **Apply** changes a path.
        blocks.append(
            "I have put a proposal above. Nothing has changed on your path yet — "
            "look it over and tap **Apply** if it is what you wanted."
        )
    blocks.append("Ask a follow-up if any part of that is still unclear.")
    return "\n\n".join(blocks)


def _forced_proposal(
    asked: str, question: str, prompt: str
) -> PathProposalPayload | None:
    """The payload a shaping proposal sentinel in ``asked`` forces, if any.

    ``question`` is the cleaned ask (the deterministic seed); ``prompt`` is the
    whole request, where the shaping deps block states the engagement boundary.
    Add wins over revise if both are present — an arbitrary but fixed order, so
    the combination is deterministic rather than undefined.
    """
    if FORCE_PROPOSAL_ADD in asked:
        return build_stub_addition_proposal(
            question, insert_at_position=_read_first_shapeable_position(prompt)
        )
    if FORCE_PROPOSAL_REVISE in asked:
        return build_stub_revision_proposal(
            question, lesson_id=_read_first_shapeable_lesson_id(prompt)
        )
    return None


def _tool_call_deltas(
    name: str, payload: TutorCheckPayload | PathProposalPayload
) -> Iterator[DeltaToolCalls]:
    """``payload`` as a call to the tool ``name``, split across two deltas.

    Two rather than one so the consumer's tool-argument accumulation is
    exercised, not just a single whole-payload part.
    """
    args = json.dumps(payload)
    split = len(args) // 2
    yield {0: DeltaToolCall(name=name, json_args=args[:split])}
    yield {0: DeltaToolCall(json_args=args[split:])}


async def _stub_stream(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str | DeltaToolCalls]:
    """The deterministic streamed callback — the tutor and shaping branches.

    Phase 2 TDD §11/D10 for the tutor; Phase 2B TDD §11/D12 for shaping. No
    output-schema dispatch: only these two stream, and the sentinel in the
    question is what tells them apart when it matters — a shaping turn with no
    sentinel is answered like any other question, which is what keeps the
    in-lesson tutor bit-identical (W21).

    Sentinel precedence, in the order applied below: ``[force-tutor-check]`` and
    then ``[force-proposal-add]`` / ``[force-proposal-revise]`` take the tool-call
    leg (nothing else can run on it — there is no text); then
    ``[force-tutor-refusal]`` and ``[force-shaping-decline]`` choose the text;
    then ``[force-tutor-failure]`` / ``[force-shaping-failure]`` interrupt
    whichever text is being streamed. So combining a tool sentinel with a failure
    fails the leg *after* the tool call, which is the only reading that keeps each
    sentinel's meaning intact.
    """
    asked = _last_user_text(messages)
    question = clean_topic(asked)
    prompt = _prompt_text(messages, info)
    check_posed = _tool_called_this_run(messages, TUTOR_CHECK_TOOL_NAME)
    proposal_made = _tool_called_this_run(messages, PROPOSE_PATH_EDIT_TOOL_NAME)

    if FORCE_TUTOR_CHECK in asked and not check_posed:
        check = build_stub_tutor_check(question)
        for delta in _tool_call_deltas(TUTOR_CHECK_TOOL_NAME, check):
            yield delta
        return

    proposal = _forced_proposal(asked, question, prompt) if not proposal_made else None
    if proposal is not None:
        for delta in _tool_call_deltas(PROPOSE_PATH_EDIT_TOOL_NAME, proposal):
            yield delta
        return

    if FORCE_TUTOR_REFUSAL in asked:
        reply = TUTOR_REFUSAL_REPLY
    elif FORCE_SHAPING_DECLINE in asked:
        reply = SHAPING_DECLINED_EDIT_REPLY
    else:
        reply = _build_tutor_reply(
            question,
            passage_slice=stub_passage_slice(prompt),
            lesson_error=LESSON_ERROR_FALSE_CLAIM in prompt,
            check_posed=check_posed,
            proposal_made=proposal_made,
        )

    forced_failure = FORCE_TUTOR_FAILURE in asked or FORCE_SHAPING_FAILURE in asked
    fail_after = _FAILURE_AFTER_DELTAS if forced_failure else None
    for index, delta in enumerate(_split_deltas(reply)):
        if index == fail_after:
            kind = "tutor" if FORCE_TUTOR_FAILURE in asked else "shaping"
            raise StubModelForcedError(
                f"forced {kind} failure after {index} deltas (mid-stream)"
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
