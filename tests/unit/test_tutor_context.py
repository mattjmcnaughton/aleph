"""Unit coverage for the context seam's pure core (Phase 2 TDD §5.2, D6).

``assemble_lesson_context`` itself is DB-backed and lives in the integration
tier (``tests/integration/test_tutor_context.py``); everything it does *after*
the reads is pure, and that is what this module pins — cheaply, in the fast
gate:

* :func:`~aleph.services.tutor_context.build_message_history` — turn pairing,
  the ``TUTOR_CONTEXT_TURNS`` window (most recent N, oldest first, older turns
  **dropped not summarized**), and the part shapes the history is allowed to
  contain.
* :func:`~aleph.services.tutor_context.render_tutor_check` — a prior Tutor
  check's compact text form (§5.1: text, never tool parts).

**Why the part-shape assertions are load-bearing.** Two reasons, and neither is
"a carried tool part would suppress this turn's check" — the "already posed?"
scans in ``agents/tutor.py`` and ``services/stub_model.py`` both bound at the
*last* ``UserPromptPart``, which is the current question appended after this
history, so anything carried here sits before that boundary and is already
excluded. What actually depends on the shape is:

1. ``agents/tutor.py`` delivers its whole prompt through pydantic-ai's
   ``instructions`` seam precisely because the carried history holds no system
   parts — a ``SystemPromptPart`` here would restate a stale Attempt regime on
   every later turn.
2. Provider adapters differ in how they map a ``ToolCallPart`` with no matching
   tool return, which is the shape a carried-in Tutor check would have; text is
   the one shape every adapter renders identically.

So "learner/tutor plain text only" is an invariant of this seam, asserted here
rather than left to a docstring.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from aleph.models import Message, MessageRole, MessageSource
from aleph.services.tutor_context import build_message_history, render_tutor_check

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.messages import ModelMessage

TUTOR_CHECK: dict[str, Any] = {
    "stem": "Which binding owns the String after a move?",
    "options": ["The first", "The second", "Neither"],
    "correct_index": 1,
    "explanation": "A move transfers ownership to the new binding.",
    "answered_index": None,
}


def learner(content: str) -> Message:
    """An in-memory learner row (no session — the helpers under test are pure)."""
    return Message(
        role=MessageRole.LEARNER, content=content, source=MessageSource.TYPED
    )


def tutor(content: str, *, tutor_check: dict[str, Any] | None = None) -> Message:
    """An in-memory tutor row, optionally carrying a posed Tutor check."""
    return Message(role=MessageRole.TUTOR, content=content, tutor_check=tutor_check)


def thread(turn_count: int) -> list[Message]:
    """``turn_count`` complete turns, numbered so order is assertable."""
    rows: list[Message] = []
    for index in range(1, turn_count + 1):
        rows.append(learner(f"question {index}"))
        rows.append(tutor(f"reply {index}"))
    return rows


def texts(history: Sequence[ModelMessage]) -> list[str]:
    """Every part's text, in order — the flattened conversation."""
    flattened: list[str] = []
    for message in history:
        for part in message.parts:
            content = getattr(part, "content", "")
            flattened.append(content if isinstance(content, str) else str(content))
    return flattened


# --------------------------------------------------------------------------- #
# Window selection (AC: exactly N most-recent pairs, oldest-first)
# --------------------------------------------------------------------------- #


def test_empty_thread_yields_empty_history() -> None:
    assert build_message_history([], turns=10) == []


def test_window_keeps_the_n_most_recent_turns_oldest_first() -> None:
    carried = texts(build_message_history(thread(12), turns=10))

    assert len(carried) == 20  # noqa: PLR2004 - 10 turns, two messages each
    assert carried[:2] == ["question 3", "reply 3"]
    assert carried[-2:] == ["question 12", "reply 12"]


def test_a_thread_exactly_the_window_length_is_carried_whole() -> None:
    """The boundary case: N turns, window N — every pair carried, none dropped."""
    carried = texts(build_message_history(thread(10), turns=10))

    assert len(carried) == 20  # noqa: PLR2004 - 10 turns, two messages each
    assert carried[:2] == ["question 1", "reply 1"]
    assert carried[-2:] == ["question 10", "reply 10"]


def test_a_short_thread_is_carried_whole() -> None:
    assert texts(build_message_history(thread(2), turns=10)) == [
        "question 1",
        "reply 1",
        "question 2",
        "reply 2",
    ]


def test_window_of_one_keeps_only_the_latest_turn() -> None:
    assert texts(build_message_history(thread(5), turns=1)) == [
        "question 5",
        "reply 5",
    ]


def test_older_turns_are_dropped_not_summarized() -> None:
    """D6: dropping is the whole mechanism — nothing stands in for turn 1."""
    carried = texts(build_message_history(thread(12), turns=10))

    assert "question 1" not in carried
    assert "reply 1" not in carried
    assert "question 2" not in carried
    assert len(carried) == 20  # noqa: PLR2004 - no summary message was added


def test_a_dangling_learner_message_is_not_carried() -> None:
    """A turn is a *pair* (D2). An unpaired row cannot close a turn."""
    rows = [*thread(1), learner("asked but never answered")]

    assert texts(build_message_history(rows, turns=10)) == ["question 1", "reply 1"]


def test_an_orphan_tutor_message_is_not_carried() -> None:
    rows = [tutor("reply with no question"), *thread(1)]

    assert texts(build_message_history(rows, turns=10)) == ["question 1", "reply 1"]


# --------------------------------------------------------------------------- #
# Part shapes (the invariant `agents/tutor.py` and the stub both rely on)
# --------------------------------------------------------------------------- #


def test_turns_alternate_request_and_response() -> None:
    history = build_message_history(thread(2), turns=10)

    assert [type(message) for message in history] == [
        ModelRequest,
        ModelResponse,
        ModelRequest,
        ModelResponse,
    ]
    assert all(isinstance(part, UserPromptPart) for part in history[0].parts)
    assert all(isinstance(part, TextPart) for part in history[1].parts)


def test_history_carries_no_system_or_tool_parts() -> None:
    rows = [
        *thread(3),
        learner("quiz me"),
        tutor("Here you go.", tutor_check=TUTOR_CHECK),
    ]

    history = build_message_history(rows, turns=10)

    kinds = {type(part).__name__ for message in history for part in message.parts}
    assert kinds == {"UserPromptPart", "TextPart"}


def test_window_defaults_to_the_configured_turn_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``turns`` is optional; it defaults to ``TUTOR_CONTEXT_TURNS``."""
    from aleph.config import settings

    monkeypatch.setattr(settings, "tutor_context_turns", 2)

    assert texts(build_message_history(thread(4))) == [
        "question 3",
        "reply 3",
        "question 4",
        "reply 4",
    ]


def test_rejects_a_non_positive_window() -> None:
    with pytest.raises(ValueError, match="turns"):
        build_message_history(thread(2), turns=0)


# --------------------------------------------------------------------------- #
# A prior Tutor check renders as compact text (§5.1, AC 3)
# --------------------------------------------------------------------------- #


def test_render_tutor_check_carries_stem_options_and_correct_option() -> None:
    rendered = render_tutor_check(TUTOR_CHECK)

    assert TUTOR_CHECK["stem"] in rendered
    for index, option in enumerate(TUTOR_CHECK["options"]):
        assert f"[{index}] {option}" in rendered
    assert "Correct option index: 1" in rendered


def test_render_tutor_check_omits_the_answer_line_when_unanswered() -> None:
    assert "answer index" not in render_tutor_check(TUTOR_CHECK).lower()


def test_render_tutor_check_includes_the_learners_answer_when_present() -> None:
    rendered = render_tutor_check({**TUTOR_CHECK, "answered_index": 2})

    assert "Learner's answer index: 2" in rendered


def test_a_posed_check_is_appended_to_its_own_tutor_message() -> None:
    rows = [
        learner("quiz me"),
        tutor("Here you go.", tutor_check={**TUTOR_CHECK, "answered_index": 0}),
        learner("why is that right?"),
        tutor("Because ownership is unique."),
    ]

    carried = texts(build_message_history(rows, turns=10))

    assert carried[0] == "quiz me"
    assert carried[1].startswith("Here you go.")
    assert TUTOR_CHECK["stem"] in carried[1]
    assert "Learner's answer index: 0" in carried[1]
    # The later turn is untouched — only the message that posed it carries it.
    assert carried[3] == "Because ownership is unique."


def test_a_tutor_message_without_a_check_is_carried_verbatim() -> None:
    assert texts(build_message_history(thread(1), turns=10))[1] == "reply 1"
