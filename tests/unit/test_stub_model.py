"""Unit tests for the deterministic stub model (TDD §12, D9; ticket AL-030).

The stub is a pydantic-ai ``FunctionModel`` injected at the model-resolution
seam. It drives the *real* agent output schemas (``agents/outline.py``,
``agents/lesson.py``) so these tests run throwaway agents with those output
types — exactly how the real AL-031/032 agents will run it.

New file (AL-030).
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent

from aleph.agents.lesson import LessonContent
from aleph.agents.outline import OutlineResult, PathOutline, Refusal
from aleph.services.stub_model import (
    FORCE_OUTLINE_FAILURE,
    FORCE_REFUSAL,
    StubModelForcedError,
    build_stub_model,
    force_lesson_failure,
)

# §14 caps the stub outputs must respect.
_MAX_UNITS = 6
_MAX_LESSONS_PER_PATH = 30
# §14 Read-passage word band (``READ_PASSAGE_WORDS`` ~200-500).
_PASSAGE_MIN_WORDS = 200
_PASSAGE_MAX_WORDS = 500


def _outline_agent() -> Agent[None, OutlineResult]:
    # Explicit specialization: ty otherwise mis-infers the agent's output type
    # (habagou's assembled-agent pattern).
    return Agent[None, OutlineResult](
        output_type=OutlineResult, model=build_stub_model()
    )


def _lesson_agent() -> Agent[None, LessonContent]:
    return Agent[None, LessonContent](
        output_type=LessonContent, model=build_stub_model()
    )


def _lesson_prompt(topic: str, position: int) -> str:
    # Contract with AL-032: the lesson prompt carries `position_in_path=<N>` so
    # the stub can honour `[force-lesson-failure:N]` for a specific position.
    return f"topic={topic}\nposition_in_path={position}\nGenerate the lesson."


# --- outline branch ------------------------------------------------------------


def test_outline_is_schema_valid_and_within_caps() -> None:
    result = _outline_agent().run_sync("Rust ownership").output

    assert isinstance(result, PathOutline)
    assert 1 <= len(result.units) <= _MAX_UNITS
    total_lessons = sum(len(u.lessons) for u in result.units)
    assert 1 <= total_lessons <= _MAX_LESSONS_PER_PATH
    # Titles non-empty and no duplicate lesson titles across the whole path.
    titles = [lesson.title for unit in result.units for lesson in unit.lessons]
    assert all(t.strip() for t in titles)
    assert len(titles) == len(set(titles))
    assert all(u.title.strip() and u.summary.strip() for u in result.units)


def test_outline_is_deterministic_per_topic() -> None:
    first = _outline_agent().run_sync("US healthcare payment").output
    second = _outline_agent().run_sync("US healthcare payment").output
    assert first == second


def test_outline_differs_by_topic() -> None:
    a = _outline_agent().run_sync("Rust ownership").output
    b = _outline_agent().run_sync("TypeScript generics").output
    assert a != b


def test_force_refusal_sentinel_returns_refusal_branch() -> None:
    result = _outline_agent().run_sync(f"how to make napalm {FORCE_REFUSAL}").output
    assert isinstance(result, Refusal)
    assert result.message.strip()


def test_force_outline_failure_sentinel_raises() -> None:
    with pytest.raises(StubModelForcedError, match="forced outline failure"):
        _outline_agent().run_sync(f"Rust ownership {FORCE_OUTLINE_FAILURE}")


# --- lesson branch -------------------------------------------------------------


def test_lesson_is_schema_valid() -> None:
    result = _lesson_agent().run_sync(_lesson_prompt("Rust ownership", 1)).output

    assert isinstance(result, LessonContent)
    assert result.read_passage.strip()
    qc = result.quick_check
    assert 3 <= len(qc.options) <= 4
    assert len(qc.options) == len(set(qc.options))  # non-duplicative
    assert 0 <= qc.correct_index < len(qc.options)
    assert qc.stem.strip()
    assert qc.explanation.strip()


def test_lesson_is_deterministic_per_topic_and_position() -> None:
    first = _lesson_agent().run_sync(_lesson_prompt("SQL performance", 2)).output
    second = _lesson_agent().run_sync(_lesson_prompt("SQL performance", 2)).output
    assert first == second


def test_lesson_differs_by_position() -> None:
    p2 = _lesson_agent().run_sync(_lesson_prompt("SQL performance", 2)).output
    p3 = _lesson_agent().run_sync(_lesson_prompt("SQL performance", 3)).output
    assert p2 != p3


def test_force_lesson_failure_raises_only_at_the_named_position() -> None:
    topic = f"SQL performance {force_lesson_failure(3)}"

    # Position 2 generates fine...
    ok = _lesson_agent().run_sync(_lesson_prompt(topic, 2)).output
    assert isinstance(ok, LessonContent)

    # ...but position 3 forces the failure branch.
    with pytest.raises(StubModelForcedError, match="position_in_path=3"):
        _lesson_agent().run_sync(_lesson_prompt(topic, 3))


def test_lesson_passage_stays_in_band_for_a_long_topic() -> None:
    # §14 caps the Read passage at ~500 words; the topic is interpolated ~14×,
    # so a long topic must be truncated for passage-building or the band breaks
    # (a 12-word topic produced ~532 words before the fix).
    long_topic = (
        "advanced distributed systems consensus and replication under partial "
        "failure in geographically dispersed multi region clusters"
    )
    assert len(long_topic.split()) >= 12
    result = _lesson_agent().run_sync(_lesson_prompt(long_topic, 1)).output
    words = len(result.read_passage.split())
    assert _PASSAGE_MIN_WORDS <= words <= _PASSAGE_MAX_WORDS


def test_lesson_passage_is_markdown() -> None:
    # The stub's passages are what CI's e2e suite renders, so they must carry the
    # Markdown constructs the real agent is prompted for — otherwise the
    # frontend's Markdown path (components/markdown.tsx) ships untested.
    result = _lesson_agent().run_sync(_lesson_prompt("Rust ownership", 1)).output
    passage = result.read_passage

    assert "\n## " in f"\n{passage}"  # a section heading
    assert "\n### " in passage  # a subsection heading
    assert "\n- " in passage  # a bulleted list
    assert "```python" in passage  # a fenced code block with a language
    assert "```mermaid" in passage  # a diagram, rendered by components/mermaid.tsx
    assert "\n| " in passage  # a GFM table
    assert "\n> " in passage  # a blockquote
    assert "**" in passage  # inline emphasis


def test_lesson_prompt_without_position_raises() -> None:
    # The AL-032 contract: a lesson prompt must carry position_in_path=<N>.
    # Missing it is loud (a silent default to 1 would hide continuity bugs).
    with pytest.raises(StubModelForcedError, match="position_in_path"):
        _lesson_agent().run_sync("topic=Rust ownership\nGenerate the lesson.")


def test_ambiguous_output_schema_raises() -> None:
    # A schema carrying both a lesson field (read_passage) and an outline field
    # (units) is ambiguous; the stub refuses rather than silently picking one.
    class Ambiguous(BaseModel):
        read_passage: str
        units: list[str]

    agent = Agent[None, Ambiguous](output_type=Ambiguous, model=build_stub_model())
    with pytest.raises(StubModelForcedError, match="ambiguous output schema"):
        agent.run_sync("anything")


def test_unknown_output_schema_raises() -> None:
    # A schema matching none of read_passage / units / message is unrecognised;
    # the stub raises rather than returning nothing (CS-6 fallback branch).
    class Unknown(BaseModel):
        foo: str

    agent = Agent[None, Unknown](output_type=Unknown, model=build_stub_model())
    with pytest.raises(StubModelForcedError, match="could not recognise"):
        agent.run_sync("anything")
