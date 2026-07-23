"""Unit tests for :mod:`aleph.domains.grading` (deterministic Attempt → Outcome).

The Quick check is graded by the app, never a model (PRD §5.3), and the first
Attempt is the Outcome of record (CONTEXT.md; TDD §4). Both properties are pure
functions of plain data — tested here with no DB and no mocks.
"""

from __future__ import annotations

from aleph.domains.grading import (
    Attempt,
    Outcome,
    grade,
    outcome_of_record,
)

# -- Outcome enum --------------------------------------------------------- #


def test_outcome_values() -> None:
    assert Outcome.CORRECT == "correct"
    assert Outcome.INCORRECT == "incorrect"


# -- grade ---------------------------------------------------------------- #


def test_grade_correct_selection() -> None:
    assert grade(Attempt(selected_index=2), correct_index=2) == Outcome.CORRECT


def test_grade_incorrect_selection() -> None:
    assert grade(Attempt(selected_index=0), correct_index=2) == Outcome.INCORRECT


def test_grade_out_of_range_selection_is_incorrect() -> None:
    # A selection outside the option set can never equal the keyed index, so it
    # grades incorrect rather than raising — bounds are a validator concern (§5.1).
    assert grade(Attempt(selected_index=99), correct_index=2) == Outcome.INCORRECT


def test_grade_negative_selection_is_incorrect() -> None:
    assert grade(Attempt(selected_index=-1), correct_index=0) == Outcome.INCORRECT


# -- outcome_of_record: first-attempt-wins -------------------------------- #


def test_no_prior_uses_submitted() -> None:
    submitted = Attempt(selected_index=2)
    recorded, outcome = outcome_of_record(
        prior=None, submitted=submitted, correct_index=2
    )
    assert recorded == submitted
    assert outcome == Outcome.CORRECT


def test_prior_wins_and_ignores_a_later_correct_submission() -> None:
    # First Attempt was wrong; a later (correct) resubmission must NOT change the
    # Outcome of record — the first answer stands (CONTEXT.md).
    prior = Attempt(selected_index=0)  # wrong
    submitted = Attempt(selected_index=2)  # right, but too late
    recorded, outcome = outcome_of_record(
        prior=prior, submitted=submitted, correct_index=2
    )
    assert recorded == prior
    assert outcome == Outcome.INCORRECT


def test_prior_wins_and_ignores_a_later_incorrect_submission() -> None:
    # First Attempt was correct; a later wrong resubmission cannot undo it.
    prior = Attempt(selected_index=2)  # right
    submitted = Attempt(selected_index=0)  # wrong, but too late
    recorded, outcome = outcome_of_record(
        prior=prior, submitted=submitted, correct_index=2
    )
    assert recorded == prior
    assert outcome == Outcome.CORRECT
