"""Pure progression logic: unlock-state derivation and completion rollup (§4).

Unlock state is **derived, never stored** (TDD §4): a lesson's place on the
learner's path is a pure function of the path's ``position_in_path`` total order
and each lesson's ``completed_at``:

    complete  iff completed_at is set
    available iff it is the first incomplete lesson in position_in_path order
    locked    otherwise

**Boundary contract (composes with AL-011's data layer).** A service builds a
:class:`LessonProgress` per lesson from a row — e.g. from
``LessonRepository.list_for_path_with_effective_state`` (which already returns
``(Lesson, effective_state)`` pairs ordered by ``position_in_path``) — calls
:func:`derive_unlock_states`, and zips the returned states back onto its rows.
The result is **aligned to input order**, so that zip is correct regardless of
how the caller ordered its input. :func:`available_index` likewise returns an
**index into that same input sequence**, so a caller reads
``rows[available_index(...)]`` directly instead of searching its rows for a
returned value object.

**Linearity vs. contiguity.** §4's linearity is an *ordering* (the total order
``position_in_path`` induces), not a requirement that positions be contiguous
integers. These functions therefore derive purely off the sort order and
tolerate gaps (positions ``0, 2, 5``).

**Uniqueness is a precondition the DB owns.** ``position_in_path`` is unique per
path — the DB's ``UNIQUE (path_id, position_in_path)`` (TDD §4) guarantees it,
so "the first incomplete lesson" is always well defined. These pure functions
receive already-persisted rows and **trust that invariant rather than
re-validating it** on every call: a duplicate position is a caller bug upstream,
not an input the domain defends against. (min-based selection stays deterministic
even so.)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime


class UnlockState(StrEnum):
    """Where a lesson sits on the learner's path (CONTEXT.md: Unlock state).

    ``locked`` -> ``available`` -> ``complete``. The learner-facing axis, derived
    here; orthogonal to the stored generation state.
    """

    LOCKED = "locked"
    AVAILABLE = "available"
    COMPLETE = "complete"


@dataclass(frozen=True)
class LessonProgress:
    """A lesson's progression-relevant facts, decoupled from the ORM.

    ``position_in_path`` is the path's single total order (TDD §4);
    ``completed_at`` is the mark-complete timestamp (``None`` = not complete).
    Nothing else about a lesson matters to unlock derivation, so a service maps a
    ``Lesson`` row to just these two fields before calling in.
    """

    position_in_path: int
    completed_at: datetime | None

    @property
    def is_complete(self) -> bool:
        """A lesson is complete iff its ``completed_at`` is set (TDD §4)."""
        return self.completed_at is not None


@dataclass(frozen=True)
class CompletionSummary:
    """A completion roll-up over a set of lessons (a unit's, or a whole path's).

    Unit completion and path completion (PRD §5.4) are the **same** operation over
    a different set of lessons — pass a unit's lessons or the whole path's; the
    logic does not differ. ``completed``/``total`` also drive progress display
    ("2 of 5"); :attr:`is_complete` is the boolean rollup.
    """

    completed: int
    total: int

    @property
    def is_complete(self) -> bool:
        """True iff there is at least one lesson and every one is complete.

        An empty collection is **not** complete: a path or unit with no lessons
        is not a finished learning journey (PRD §5.4 defines completion as the
        last lesson being marked complete), so the vacuously-true "all complete"
        is deliberately excluded.
        """
        return self.total > 0 and self.completed == self.total


def available_index(lessons: Sequence[LessonProgress]) -> int | None:
    """Index (into ``lessons``) of the available lesson, or ``None``.

    The available lesson is the first incomplete one in ``position_in_path``
    order — the learner's "current" lesson (the mock's rail label). The return
    value is an **index into the input sequence** (aligned to input order, like
    :func:`derive_unlock_states`), so a caller reads ``rows[i]`` directly rather
    than re-searching its rows for a returned value object. ``None`` when the
    path is empty or fully complete.
    """
    incomplete = [
        index for index, lesson in enumerate(lessons) if not lesson.is_complete
    ]
    if not incomplete:
        return None
    return min(incomplete, key=lambda index: lessons[index].position_in_path)


def derive_unlock_states(lessons: Sequence[LessonProgress]) -> list[UnlockState]:
    """Each lesson's unlock state, aligned to the input order (TDD §4).

    complete iff ``completed_at`` set; available iff it is the first incomplete
    lesson in ``position_in_path`` order; else locked. Input order does not
    affect *which* lesson is available — positions decide it — and the returned
    list re-aligns to the input so callers can zip it back onto their rows.
    """
    available = available_index(lessons)
    states: list[UnlockState] = []
    for index, lesson in enumerate(lessons):
        if lesson.is_complete:
            states.append(UnlockState.COMPLETE)
        elif index == available:
            states.append(UnlockState.AVAILABLE)
        else:
            states.append(UnlockState.LOCKED)
    return states


def next_lesson(
    lessons: Sequence[LessonProgress], *, after_position: int
) -> LessonProgress | None:
    """The lesson immediately after ``after_position`` in total order.

    Navigation helper (e.g. advance the learner after mark-complete): the
    successor by ``position_in_path``, independent of completion/unlock state.
    Returns the successor lesson itself — its ``position_in_path`` is the
    total-order key the caller navigates by, so (unlike the current lesson) no
    index back-reference is needed. ``None`` when ``after_position`` is at or
    past the last lesson, or the path is empty. Tolerates gaps.
    """
    successors = [
        lesson for lesson in lessons if lesson.position_in_path > after_position
    ]
    if not successors:
        return None
    return min(successors, key=lambda lesson: lesson.position_in_path)


def summarize_completion(lessons: Sequence[LessonProgress]) -> CompletionSummary:
    """Count completed vs. total lessons in a set (a unit's, or a path's).

    The single completion rollup (PRD §5.4): call ``.is_complete`` on the result
    for the boolean "unit/path complete" test, or read ``completed``/``total``
    for progress display. Unit vs. path completion is the same operation over a
    different set of lessons — the caller chooses which lessons to pass.
    """
    total = len(lessons)
    completed = sum(1 for lesson in lessons if lesson.is_complete)
    return CompletionSummary(completed=completed, total=total)
