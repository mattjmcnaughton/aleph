"""Pure **Change**-payload logic: position shifts and their inverses (AL-321).

A :class:`~aleph.models.PathChange` row's ``payload`` is "the applied operations
**plus inverses**" (Phase 2B TDD §4/D8) — the one artefact that makes a Change
self-sufficient for **Undo**. This module owns what that object *is*, and owns
it as plain data: no ORM row, no session, no config (:mod:`aleph.domains`'
boundary contract).

**Why the shape lives in ``domains/`` rather than in the shaping service.** Two
services touch it and neither may import the other. ``services/shaping.py``
*writes* it inside ``apply_change``/``undo_change``; ``services/generation.py``
*reads* one revision snapshot out of it, because D7 clears the lesson's content
at apply time and the Phase 1 lesson prompt still has to carry the **old
passage** in its revision block. A direct import either way is a cycle
(``services/shaping`` → ``services/tutor_context`` → ``services/generation``),
so the shared definition sits below both, where it is also trivially testable.

**Positions are the fiddly part** (TDD §14 names it the fiddliest correctness
surface in the phase). ``UNIQUE (path_id, position_in_path)`` is non-deferrable
and Postgres checks it per updated row, so a shift is a *plan* — an ordered list
of single-row moves — not a set-based ``UPDATE … + n``. :func:`plan_insertion_shifts`
emits that plan **descending** (D6) and :func:`reverse_shifts` emits its undo
**ascending** (D8); in both directions every move lands on a slot that is already
free, which is why no deferred constraint and no temporary offset is needed.
The property-style round trip in ``tests/unit/test_change_payload.py`` models the
constraint explicitly and is the insurance the TDD asks for.

Ids are ``str`` throughout: this data round-trips through JSONB, so it is stored
in the shape it is stored in, and the services stringify/parse at their own edge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

# The payload's top-level keys. ``operations`` and ``summary`` are **not**
# private to this module: ``services/tutor_context.py`` reads both off the row to
# render the Change history the shaper sees (its ``CHANGE_OPERATIONS_KEY`` /
# ``CHANGE_SUMMARY_KEY``, which are these strings). They are restated here as
# literals rather than imported from that service because a domain module
# importing a service is exactly what this package forbids — the coupling is a
# wire format, and the integration suite pins that the two agree.
OPERATIONS_KEY = "operations"
SUMMARY_KEY = "summary"
INVERSE_KEY = "inverse"


@dataclass(frozen=True)
class PositionShift:
    """One lesson's move along ``position_in_path`` (the path's total order).

    Recorded rather than recomputed at undo time: the inverse has to describe
    what *this* Change did, and a path that has moved on since must not be
    re-derived from — the row is the source of truth (D8).
    """

    lesson_id: str
    from_position: int
    to_position: int

    def as_payload(self) -> dict[str, Any]:
        return {
            "lesson_id": self.lesson_id,
            "from": self.from_position,
            "to": self.to_position,
        }


@dataclass(frozen=True)
class UnitSlot:
    """One lesson's move along ``position_in_unit`` (display order in its unit).

    A separate type from :class:`PositionShift` for a reason that is not
    cosmetic: ``position_in_unit`` carries **no unique constraint**, so these
    moves have no ordering requirement at all, and giving them the same type
    would invite a caller to run them through the descending/ascending machinery
    that only ``position_in_path`` needs.
    """

    lesson_id: str
    from_position: int
    to_position: int

    def as_payload(self) -> dict[str, Any]:
        return {
            "lesson_id": self.lesson_id,
            "from": self.from_position,
            "to": self.to_position,
        }


@dataclass(frozen=True)
class QuickCheckSnapshot:
    """A Quick check exactly as it was, so undo can put the row back verbatim."""

    stem: str
    options: tuple[str, ...]
    correct_index: int
    explanation: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "stem": self.stem,
            "options": list(self.options),
            "correct_index": self.correct_index,
            "explanation": self.explanation,
        }

    @classmethod
    def from_payload(cls, payload: Any) -> QuickCheckSnapshot | None:
        if not isinstance(payload, dict):
            return None
        return cls(
            stem=str(payload.get("stem", "")),
            options=tuple(str(option) for option in payload.get("options") or ()),
            correct_index=int(payload.get("correct_index", 0)),
            explanation=str(payload.get("explanation", "")),
        )


@dataclass(frozen=True)
class RevisionSnapshot:
    """A lesson's pre-**Revision** state, plus the instruction that replaced it.

    Two readers, one object (see the module docstring):

    * **Undo** restores ``title``/``read_passage``/``generated_at`` and re-creates
      the Quick check row, which is what "restores exactly" (PRD §5.5) means
      transactionally.
    * **Generation** reads ``read_passage`` back for the lesson prompt's revision
      block (D7): apply clears the lesson's content, so the old passage exists
      only here, and without it a revision would *re-invent* the lesson instead
      of re-pitching it.

    ``read_passage``, ``generated_at`` and ``quick_check`` are all optional: a
    Revision of a lesson Phase 1 has not generated yet is legal (it is unengaged,
    which is the only boundary that matters — D2), and there is simply nothing to
    snapshot.

    **What is deliberately not snapshotted: the generation axis's error state.**
    ``generation_state`` and ``generation_error`` are not fields here, so undo
    restores the state *implied* by the snapshot —
    :meth:`~aleph.repositories.lessons.LessonRepository.restore_revision` writes
    ``generated`` when there is a passage and ``ungenerated`` when there is not.
    The one case that differs from "byte-identical" is a lesson that had
    **failed** to generate before it was revised: it comes back ``ungenerated``
    with its error cleared rather than ``failed`` with the old message. That is
    a deviation this phase accepts rather than a gap it missed — ``failed`` is
    the retryable branch of Phase 1's state machine and ``ungenerated`` is its
    start, so the row simply regenerates on the next poll, which is what the
    learner wanted from the failed lesson in the first place. Storing the pair
    would make undo restore a stale error message onto a row nothing is going to
    re-read it from.
    """

    lesson_id: str
    title: str
    read_passage: str | None
    generated_at: str | None
    instruction: str
    quick_check: QuickCheckSnapshot | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "lesson_id": self.lesson_id,
            "title": self.title,
            "read_passage": self.read_passage,
            "generated_at": self.generated_at,
            "instruction": self.instruction,
            "quick_check": (
                None if self.quick_check is None else self.quick_check.as_payload()
            ),
        }

    @classmethod
    def from_payload(cls, payload: Any) -> RevisionSnapshot | None:
        if not isinstance(payload, dict) or not payload.get("lesson_id"):
            return None
        passage = payload.get("read_passage")
        generated_at = payload.get("generated_at")
        return cls(
            lesson_id=str(payload["lesson_id"]),
            title=str(payload.get("title", "")),
            read_passage=None if passage is None else str(passage),
            generated_at=None if generated_at is None else str(generated_at),
            instruction=str(payload.get("instruction", "")),
            quick_check=QuickCheckSnapshot.from_payload(payload.get("quick_check")),
        )


@dataclass(frozen=True)
class ChangeInverse:
    """Everything **Undo** needs, and nothing it can get anywhere else (D8).

    * ``added_lesson_ids`` / ``added_unit_ids`` — the rows to delete. An
      in-flight generation of a deleted lesson ends in a guarded zero-row
      ``UPDATE`` and is dropped (TDD §5.7), so there is nothing to coordinate.
    * ``shifts`` — the ``position_in_path`` moves, in the order they were made;
      undo runs :func:`reverse_shifts` over them.
    * ``slots`` — the ``position_in_unit`` moves (unconstrained, any order).
    * ``units`` — ``(unit_id, position)`` for every unit whose display position
      the apply renumbered, as it was *before*. Restored verbatim rather than
      recomputed, for the same reason the shifts are.
    * ``revisions`` — one :class:`RevisionSnapshot` per revised lesson.

    Every field defaults to empty, and :meth:`from_payload` returns an empty
    inverse for a row that carries none: a Change written by an older shape must
    degrade into "there is nothing to reverse", never into an exception on a live
    request.
    """

    added_lesson_ids: tuple[str, ...] = ()
    added_unit_ids: tuple[str, ...] = ()
    shifts: tuple[PositionShift, ...] = ()
    slots: tuple[UnitSlot, ...] = ()
    units: tuple[tuple[str, int], ...] = ()
    revisions: tuple[RevisionSnapshot, ...] = field(default=())

    def as_payload(self) -> dict[str, Any]:
        return {
            "added_lesson_ids": list(self.added_lesson_ids),
            "added_unit_ids": list(self.added_unit_ids),
            "shifts": [shift.as_payload() for shift in self.shifts],
            "slots": [slot.as_payload() for slot in self.slots],
            "units": [[unit_id, position] for unit_id, position in self.units],
            "revisions": [revision.as_payload() for revision in self.revisions],
        }

    @classmethod
    def from_payload(cls, payload: Any) -> ChangeInverse:
        """The inverse stored on a change row, or an empty one (defensively)."""
        if not isinstance(payload, dict):
            return cls()
        inverse = payload.get(INVERSE_KEY)
        if not isinstance(inverse, dict):
            return cls()
        return cls(
            added_lesson_ids=tuple(
                str(value) for value in inverse.get("added_lesson_ids") or ()
            ),
            added_unit_ids=tuple(
                str(value) for value in inverse.get("added_unit_ids") or ()
            ),
            shifts=tuple(
                PositionShift(
                    lesson_id=str(entry["lesson_id"]),
                    from_position=int(entry["from"]),
                    to_position=int(entry["to"]),
                )
                for entry in inverse.get("shifts") or ()
                if isinstance(entry, dict) and entry.get("lesson_id")
            ),
            slots=tuple(
                UnitSlot(
                    lesson_id=str(entry["lesson_id"]),
                    from_position=int(entry["from"]),
                    to_position=int(entry["to"]),
                )
                for entry in inverse.get("slots") or ()
                if isinstance(entry, dict) and entry.get("lesson_id")
            ),
            units=tuple(
                (str(entry[0]), int(entry[1]))
                for entry in inverse.get("units") or ()
                if isinstance(entry, (list, tuple)) and len(entry) == 2
            ),
            revisions=tuple(
                snapshot
                for snapshot in (
                    RevisionSnapshot.from_payload(entry)
                    for entry in inverse.get("revisions") or ()
                )
                if snapshot is not None
            ),
        )

    def revision_for(self, lesson_id: str) -> RevisionSnapshot | None:
        """This Change's snapshot of ``lesson_id``, if it revised that lesson."""
        for revision in self.revisions:
            if revision.lesson_id == lesson_id:
                return revision
        return None


def change_payload(
    *,
    operations: Sequence[Any],
    summary: str,
    inverse: ChangeInverse,
) -> dict[str, Any]:
    """The whole stored payload: the applied Proposal, plus how to reverse it.

    ``operations`` and ``summary`` stay at the top level in the *proposal's* own
    shape, deliberately: ``services/tutor_context.py`` renders the Change history
    the shaper reads straight off these two keys, so an applied Change reads as
    the learner's own sentence rather than as a derived one.
    """
    return {
        OPERATIONS_KEY: list(operations),
        SUMMARY_KEY: summary,
        INVERSE_KEY: inverse.as_payload(),
    }


def plan_insertion_shifts(
    positions: Iterable[tuple[str, int]], *, insert_at: int, count: int
) -> tuple[PositionShift, ...]:
    """The ``position_in_path`` moves that open ``count`` slots at ``insert_at``.

    Ordered **descending** by current position (D6): each move lands on a slot
    the previous move has already vacated (or that was never occupied), so the
    non-deferrable ``UNIQUE (path_id, position_in_path)`` never fires mid-plan.
    Ascending would collide on the very first row of a contiguous path.

    ``positions`` is ``(lesson_id, position_in_path)`` for the path's lessons, in
    any order; only those at or after ``insert_at`` move. Positions are treated as
    an *ordering*, not a contiguous range (§4), so a gapped path plans correctly
    and an insertion past the end plans nothing.
    """
    moving = sorted(
        (pair for pair in positions if pair[1] >= insert_at),
        key=lambda pair: pair[1],
        reverse=True,
    )
    return tuple(
        PositionShift(
            lesson_id=lesson_id, from_position=position, to_position=position + count
        )
        for lesson_id, position in moving
    )


def reverse_shifts(shifts: Sequence[PositionShift]) -> tuple[PositionShift, ...]:
    """The undo of ``shifts``: the same moves swapped, in **reverse order** (D8).

    Undoing a sequence of moves is performing their inverses last-first — the
    only rule that is correct in general, and it degrades to exactly what D8
    describes for the ordinary case: one insertion's plan is descending, so
    reversing it is **ascending**, each move landing on the slot its predecessor
    just vacated (the added rows having been deleted first).

    Sorting by target position instead would be wrong the moment one Change
    carries **two** Additions, because then a lesson appears in the plan more
    than once and its moves have to be undone in the order they were made:
    a global sort interleaves the two lessons' second moves with each other's
    first, and the interleaving collides under
    ``UNIQUE (path_id, position_in_path)``. Reverse chronology cannot, because
    it is the literal inverse of a sequence that itself never collided.
    """
    return tuple(
        PositionShift(
            lesson_id=shift.lesson_id,
            from_position=shift.to_position,
            to_position=shift.from_position,
        )
        for shift in reversed(shifts)
    )
