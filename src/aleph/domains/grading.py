"""Pure Quick-check grading: deterministic Attempt → Outcome (PRD §5.3, §4).

A Quick check is graded by the app, never a model call (PRD §5.3): the Outcome
is a pure function of the selected option and the keyed correct option. And the
**first Attempt is the Outcome of record** (CONTEXT.md; TDD §4) — a resubmission
never changes it.

**Boundary contract.** A service maps a persisted attempt row to :class:`Attempt`
(just its ``selected_index``) and passes the Quick check's ``correct_index``. The
storage layer (AL-011's ``AttemptRepository.record``, one Attempt per learner via
``INSERT ... ON CONFLICT DO NOTHING``) enforces first-wins durably;
:func:`outcome_of_record` is the same rule stated purely, so a service can resolve
the Outcome of record without trusting a client's resubmitted index.

**Source of truth (for AL-051).** ``selected_index`` — plus the Quick check's
keyed ``correct_index`` — is authoritative. The ``attempts.is_correct`` column
(TDD §4) is a **write-time denormalization** of ``grade(...) == Outcome.CORRECT``,
kept only so metrics queries (activation, §7) need not join ``quick_checks``; it
is a cache, not the truth. Always resolve the Outcome of record by re-deriving
through :func:`grade` / :func:`outcome_of_record`, never by trusting a stored
bool that could drift from the keyed answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Outcome(StrEnum):
    """The result of an Attempt (CONTEXT.md): correct or incorrect.

    Formative and non-gating — it reveals the explanation and lets the learner
    proceed either way; it does not affect unlock state.
    """

    CORRECT = "correct"
    INCORRECT = "incorrect"


@dataclass(frozen=True)
class Attempt:
    """A learner's answer to a Quick check: the index of the option selected.

    The pure-data view of CONTEXT.md's Attempt — no id, no timestamp, no ORM. A
    service maps a persisted attempt (or an incoming submission) to this before
    grading.
    """

    selected_index: int


def grade(attempt: Attempt, *, correct_index: int) -> Outcome:
    """Deterministically grade one Attempt against the keyed correct option.

    ``CORRECT`` iff the selected index equals ``correct_index``, else
    ``INCORRECT``. An out-of-range or negative ``selected_index`` simply cannot
    equal the keyed index and grades ``INCORRECT`` — option-count bounds are a
    validator's concern (§5.1), not grading's.
    """
    return (
        Outcome.CORRECT
        if attempt.selected_index == correct_index
        else Outcome.INCORRECT
    )


def outcome_of_record(
    *, prior: Attempt | None, submitted: Attempt, correct_index: int
) -> tuple[Attempt, Outcome]:
    """Resolve the Attempt of record and its Outcome under first-attempt-wins.

    ``prior`` is the learner's already-recorded first Attempt (``None`` if this
    is their first answer). Per CONTEXT.md the first answer is the Outcome of
    record; a resubmission never overwrites it — so when ``prior`` exists it wins
    and ``submitted`` is ignored for scoring. Returns the recorded Attempt and
    its deterministic Outcome.
    """
    of_record = prior if prior is not None else submitted
    return of_record, grade(of_record, correct_index=correct_index)
