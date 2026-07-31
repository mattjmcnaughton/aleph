"""Pure engagement derivation — the immutability boundary (Phase 2B TDD D2).

**Engaged** (CONTEXT.md): a lesson with a recorded **Attempt** or marked
complete. It is the line every shaping operation respects — engaged content is
never added before, revised, or removed, and engaging with a **Change**'s
content ends its undo window.

**One predicate, three call sites.** D2 makes engagement *derived from existing
columns, never stored*, and requires it be enforced identically at proposal
validation, at **Apply**, and at **Undo**. :func:`is_engaged` is that one
predicate; everything else here is a derivation *of* it, never a
re-statement. There is deliberately no ``LessonEngagement.is_engaged``
property: a second spelling is exactly how three call sites start disagreeing.

**Viewing is deliberately not engagement** (D2). Reading a lesson leaves no row,
and a learner-initiated **Revision** of a merely-viewed lesson is the feature
working, not a leak.

Boundary contract (see :mod:`aleph.domains`): a service maps rows — a
``Lesson``'s ``position_in_path`` and ``completed_at``, plus whether an Attempt
exists on its Quick check — into :class:`LessonEngagement` before calling in.
The domain never sees an ORM object, which is what lets the shaper agent's
validation, the apply path, and the evals share it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime


@dataclass(frozen=True)
class LessonEngagement:
    """A lesson's engagement-relevant facts, decoupled from the ORM.

    ``position_in_path`` is the path's single total order (Phase 1 TDD §4);
    ``completed_at`` is the mark-complete timestamp (``None`` = not complete);
    ``has_attempt`` is whether an **Attempt** exists on the lesson's Quick check.
    Nothing else about a lesson matters to the D2 boundary — notably not its
    generation state, which is a different axis (a ``generated`` lesson is
    revisable precisely while it is unengaged).
    """

    position_in_path: int
    completed_at: datetime | None
    has_attempt: bool


def is_engaged(lesson: LessonEngagement) -> bool:
    """The D2 predicate: an Attempt exists **or** ``completed_at`` is set.

    A disjunction, not a conjunction: the Quick check is non-gating, so a
    learner may complete a lesson without attempting it, and may attempt it
    without completing it. Either signal means the learner has met the content,
    which is all the immutability boundary asks.
    """
    return lesson.has_attempt or lesson.completed_at is not None


def first_shapeable_position(lessons: Sequence[LessonEngagement]) -> int:
    """The lowest ``position_in_path`` that is not engaged (TDD §5.1).

    The boundary an **Addition** must insert at or after, precomputed so the
    shaper's prompt can state it as data rather than infer it. Derived from
    :func:`is_engaged` alone — the same predicate Apply and Undo re-check.

    Two edges, both real:

    * **Every lesson is engaged** (or the path has none) — the boundary is
      *past the end*: ``max(position_in_path) + 1``, or ``1`` for an empty path.
      A learner who has met everything can still grow the path; they simply
      cannot insert into their own history.
    * **Gaps and unordered input** — positions decide the boundary, not the
      caller's ordering, and §4's linearity is an ordering rather than a
      contiguous integer range, so both are tolerated (as in
      :mod:`~aleph.domains.progression`).

    Engagement is a prefix in practice — progression is linear, so a learner
    reaches lesson *n* only through *1…n−1* — but this deliberately does not
    assume it: it takes the *first* unengaged position, so a stray engaged
    lesson further along never re-opens an earlier slot to insertion.
    """
    unengaged = [
        lesson.position_in_path for lesson in lessons if not is_engaged(lesson)
    ]
    if unengaged:
        return min(unengaged)
    if not lessons:
        return 1
    return max(lesson.position_in_path for lesson in lessons) + 1
