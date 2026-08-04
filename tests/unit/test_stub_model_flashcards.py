"""Unit tests for the stub model's flashcard-drafting branch (Phase 3 TDD §5.2/§11).

Mirrors ``test_stub_model.py``'s outline/lesson branch shape: a throwaway
``Agent[None, FlashcardDrafts]`` bound to the stub drives the real dispatch in
``services/stub_model.py``, exercising the ``flashcard_drafts=<N>`` marker
contract (the ``position_in_path`` precedent) and the ``[force-draft-failure]``
sentinel.

New file (Phase 3 flashcards ticket 3).
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent

from aleph.agents.flashcard import (
    FlashcardCaps,
    FlashcardDrafts,
    validate_flashcard_drafts,
)
from aleph.services.stub_model import (
    FORCE_DRAFT_FAILURE,
    StubModelForcedError,
    build_stub_model,
)


def _flashcard_agent() -> Agent[None, FlashcardDrafts]:
    # Explicit specialization: ty otherwise mis-infers the agent's output type
    # (mirrors ``test_stub_model.py``'s ``_lesson_agent``/``_outline_agent``).
    return Agent[None, FlashcardDrafts](
        output_type=FlashcardDrafts, model=build_stub_model()
    )


def _prompt(topic: str, count: int) -> str:
    # Contract with agents/flashcard.py: the drafting prompt carries
    # flashcard_drafts=<N> ahead of everything else.
    return f"flashcard_drafts={count}\ntopic={topic}\nDraft the flashcards."


# --- the flashcard_drafts=<N> marker --------------------------------------------


def test_drafts_are_schema_valid_and_match_the_requested_count() -> None:
    result = _flashcard_agent().run_sync(_prompt("Rust ownership", 4)).output

    assert isinstance(result, FlashcardDrafts)
    assert len(result.cards) == 4
    for card in result.cards:
        assert card.front.strip()
        assert card.back.strip()


@pytest.mark.parametrize("count", [3, 4, 5])
def test_drafts_count_follows_the_marker(count: int) -> None:
    result = _flashcard_agent().run_sync(_prompt("Rust ownership", count)).output
    assert len(result.cards) == count


def test_drafts_pass_the_real_flashcard_validators() -> None:
    # The stub's own cards must satisfy agents/flashcard.py's validator
    # unchanged (the CI/e2e contract, TDD §12) against an unrelated stem — the
    # stub's fronts are deliberately unlike a Quick-check stem's wording.
    result = _flashcard_agent().run_sync(_prompt("Rust ownership", 4)).output
    caps = FlashcardCaps()
    stem = "An unrelated Quick check stem sharing no wording with the cards."
    assert validate_flashcard_drafts(caps, stem, result) is result


def test_drafts_are_deterministic_per_topic_and_count() -> None:
    first = _flashcard_agent().run_sync(_prompt("SQL performance", 4)).output
    second = _flashcard_agent().run_sync(_prompt("SQL performance", 4)).output
    assert first == second


def test_drafts_differ_by_topic() -> None:
    a = _flashcard_agent().run_sync(_prompt("Rust ownership", 4)).output
    b = _flashcard_agent().run_sync(_prompt("TypeScript generics", 4)).output
    assert a != b


def test_drafts_within_one_result_are_distinct() -> None:
    # Each card is seeded by its own index, so no two cards in one drafting run
    # collide.
    result = _flashcard_agent().run_sync(_prompt("Rust ownership", 5)).output
    fronts = [c.front for c in result.cards]
    assert len(fronts) == len(set(fronts)) == 5


def test_missing_marker_raises() -> None:
    # Presence is mandatory, not optional — the position_in_path posture,
    # carried over verbatim: a silent default would hide a broken prompt
    # contract rather than failing loudly at the source.
    with pytest.raises(StubModelForcedError, match="flashcard_drafts"):
        _flashcard_agent().run_sync("topic=Rust ownership\nDraft the flashcards.")


# --- [force-draft-failure] ------------------------------------------------------


def test_force_draft_failure_sentinel_raises() -> None:
    with pytest.raises(StubModelForcedError, match="forced flashcard draft failure"):
        _flashcard_agent().run_sync(_prompt(f"Rust ownership {FORCE_DRAFT_FAILURE}", 4))


def test_force_draft_failure_does_not_affect_a_different_topic() -> None:
    # Stateless like every other sentinel: only a topic carrying the literal
    # marker fires it.
    ok = _flashcard_agent().run_sync(_prompt("Rust ownership", 4)).output
    assert isinstance(ok, FlashcardDrafts)


# --- ambiguous output schema (the CS-6 fallback discipline) --------------------


def test_ambiguous_flashcard_and_outline_schema_raises() -> None:
    # A schema carrying both a flashcard field (cards) and an outline field
    # (units) is ambiguous; the stub refuses rather than silently picking one
    # (mirrors test_stub_model.py's lesson/outline ambiguity guard).
    class Ambiguous(BaseModel):
        cards: list[str]
        units: list[str]

    agent = Agent[None, Ambiguous](output_type=Ambiguous, model=build_stub_model())
    with pytest.raises(StubModelForcedError, match="ambiguous output schema"):
        agent.run_sync(_prompt("Rust ownership", 4))


def test_ambiguous_flashcard_and_lesson_schema_raises() -> None:
    class Ambiguous(BaseModel):
        cards: list[str]
        read_passage: str

    agent = Agent[None, Ambiguous](output_type=Ambiguous, model=build_stub_model())
    with pytest.raises(StubModelForcedError, match="ambiguous output schema"):
        agent.run_sync(_prompt("Rust ownership", 4))
