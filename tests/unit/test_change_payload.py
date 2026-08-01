"""The pure Change-payload domain: shifts, inverses, snapshots (AL-321).

``domains/changes.py`` is the only place that knows what a **Change** payload
*is* — the applied operations plus their inverses (Phase 2B TDD §4) — and it is
pure so both writers and readers of that payload can share it without either
importing the other (``services/shaping.py`` writes it; ``services/generation.py``
reads the revision snapshot out of it; the layering forbids a cycle between
them).

The heart of this suite is the **property-style shift/unshift round trip**
(TDD §11, D6/D8): inserting *n* lessons at a random point shifts the tail
**descending**, undo unshifts it **ascending**, and neither order may ever
transiently collide under ``UNIQUE (path_id, position_in_path)``. The collision
check is modelled explicitly — a set of occupied positions, one write at a time —
because that constraint is what makes the *order* of the updates load-bearing
rather than cosmetic.
"""

from __future__ import annotations

import random

import pytest

from aleph.domains.changes import (
    ChangeInverse,
    PositionShift,
    QuickCheckSnapshot,
    RevisionSnapshot,
    UnitSlot,
    change_payload,
    plan_insertion_shifts,
    reverse_shifts,
)

# --------------------------------------------------------------------------- #
# Shift planning (D6)
# --------------------------------------------------------------------------- #


def _positions(count: int) -> list[tuple[str, int]]:
    """``count`` lessons at the contiguous positions ``1..count``."""
    return [(f"lesson-{position}", position) for position in range(1, count + 1)]


def test_an_insertion_shifts_only_the_tail() -> None:
    """Lessons before the insertion point keep their position (CONTEXT.md)."""
    shifts = plan_insertion_shifts(_positions(4), insert_at=3, count=2)

    assert [(shift.lesson_id, shift.from_position) for shift in shifts] == [
        ("lesson-4", 4),
        ("lesson-3", 3),
    ]
    assert [shift.to_position for shift in shifts] == [6, 5]


def test_shifts_are_ordered_descending() -> None:
    """D6: descending updates never collide with the unique constraint."""
    shifts = plan_insertion_shifts(_positions(6), insert_at=2, count=1)

    assert [shift.from_position for shift in shifts] == [6, 5, 4, 3, 2]


def test_appending_past_the_end_shifts_nothing() -> None:
    shifts = plan_insertion_shifts(_positions(3), insert_at=4, count=2)

    assert shifts == ()


def test_reverse_shifts_are_ordered_ascending_and_swapped() -> None:
    """Undo walks the other way (D8) — the moves are the same, reversed."""
    shifts = plan_insertion_shifts(_positions(4), insert_at=2, count=2)

    undo = reverse_shifts(shifts)

    assert [(shift.from_position, shift.to_position) for shift in undo] == [
        (4, 2),
        (5, 3),
        (6, 4),
    ]


# --------------------------------------------------------------------------- #
# The property: a round trip that never collides (TDD §11)
# --------------------------------------------------------------------------- #


def _apply(occupied: dict[str, int], shifts: tuple[PositionShift, ...]) -> None:
    """Perform ``shifts`` one row at a time, failing on any transient collision.

    The model of ``UNIQUE (path_id, position_in_path)``: Postgres checks a
    non-deferrable unique constraint **per row**, so a plan whose intermediate
    state ever holds two lessons at one position is a plan that raises mid-apply,
    however tidy its end state looks.
    """
    for shift in shifts:
        assert occupied[shift.lesson_id] == shift.from_position
        taken = {
            position
            for lesson_id, position in occupied.items()
            if lesson_id != shift.lesson_id
        }
        assert shift.to_position not in taken, (
            f"moving {shift.lesson_id} to {shift.to_position} collides mid-plan"
        )
        occupied[shift.lesson_id] = shift.to_position


@pytest.mark.parametrize("case", range(60))
def test_shift_unshift_round_trips_over_random_insert_points(case: int) -> None:
    """Insert anywhere, any size, then undo: back to byte-identical positions."""
    rng = random.Random(case)
    lesson_count = rng.randint(0, 12)
    positions = _positions(lesson_count)
    insert_at = rng.randint(1, lesson_count + 1)
    count = rng.randint(1, 5)

    before = dict(positions)
    live = dict(positions)

    shifts = plan_insertion_shifts(positions, insert_at=insert_at, count=count)
    _apply(live, shifts)

    # The inserted lessons occupy exactly the freed slots, and nothing else does.
    freed = set(range(insert_at, insert_at + count))
    assert freed.isdisjoint(set(live.values()))
    assert len(set(live.values())) == len(live), "positions stayed unique"

    _apply(live, reverse_shifts(shifts))
    assert live == before


@pytest.mark.parametrize("case", range(40))
def test_two_insertions_in_one_change_round_trip_too(case: int) -> None:
    """One Change may carry two Additions — and then a lesson moves twice.

    The composition is what makes reverse **chronology** load-bearing rather than
    a stylistic choice: sorting the combined plan by target position interleaves
    the two passes and collides mid-undo, while undoing the sequence last-first
    cannot, because it is the literal inverse of a sequence that never collided.

    The insertion points are stated against the *same* snapshot, so the second
    one is offset by however many lessons the first landed — exactly what
    ``services/shaping.py`` does when it walks a payload's Additions ascending.
    """
    rng = random.Random(1000 + case)
    lesson_count = rng.randint(1, 10)
    positions = _positions(lesson_count)
    first_at, second_at = sorted(
        (rng.randint(1, lesson_count + 1), rng.randint(1, lesson_count + 1))
    )
    first_count, second_count = rng.randint(1, 3), rng.randint(1, 3)

    before = dict(positions)
    live = dict(positions)
    shifts: list[PositionShift] = []

    for insert_at, count in (
        (first_at, first_count),
        (second_at + first_count, second_count),
    ):
        plan = plan_insertion_shifts(
            list(live.items()), insert_at=insert_at, count=count
        )
        _apply(live, plan)
        shifts.extend(plan)

    _apply(live, reverse_shifts(shifts))
    assert live == before


def test_a_gapped_path_still_shifts_by_position_not_by_index() -> None:
    """§4's total order is an *ordering*, not a contiguous integer range."""
    gapped = [("a", 1), ("b", 5), ("c", 9)]

    shifts = plan_insertion_shifts(gapped, insert_at=5, count=3)

    assert [(shift.lesson_id, shift.to_position) for shift in shifts] == [
        ("c", 12),
        ("b", 8),
    ]


def test_unordered_input_is_planned_in_descending_order_anyway() -> None:
    shifts = plan_insertion_shifts([("c", 3), ("a", 1), ("b", 2)], insert_at=1, count=1)

    assert [shift.lesson_id for shift in shifts] == ["c", "b", "a"]


# --------------------------------------------------------------------------- #
# Payload round trip (the row is self-sufficient for undo — D8)
# --------------------------------------------------------------------------- #


def _snapshot() -> RevisionSnapshot:
    return RevisionSnapshot(
        lesson_id="11111111-1111-1111-1111-111111111111",
        title="Ownership, part 2",
        read_passage="The old passage.",
        generated_at="2026-07-31T12:00:00+00:00",
        instruction="Re-pitch it for a beginner.",
        quick_check=QuickCheckSnapshot(
            stem="Which binding owns it?",
            options=("The first", "The second", "Both"),
            correct_index=1,
            explanation="A move transfers ownership.",
        ),
    )


def _inverse() -> ChangeInverse:
    return ChangeInverse(
        added_lesson_ids=("lesson-a", "lesson-b"),
        added_unit_ids=("unit-a",),
        shifts=(PositionShift(lesson_id="lesson-c", from_position=2, to_position=4),),
        slots=(UnitSlot(lesson_id="lesson-c", from_position=2, to_position=4),),
        units=((("unit-b"), 2),),
        revisions=(_snapshot(),),
    )


def test_the_payload_carries_the_operations_and_summary_at_the_top_level() -> None:
    """``services/tutor_context`` reads both keys off the row (AL-311)."""
    payload = change_payload(
        operations=[{"lesson_id": "x", "instruction": "i", "rationale": "r"}],
        summary="Revises one lesson.",
        inverse=ChangeInverse(),
    )

    assert payload["summary"] == "Revises one lesson."
    assert payload["operations"] == [
        {"lesson_id": "x", "instruction": "i", "rationale": "r"}
    ]


def test_the_inverse_round_trips_through_json_shaped_data() -> None:
    """Undo needs no second source of truth (D8), so the row must hold it all."""
    payload = change_payload(operations=[], summary="s", inverse=_inverse())

    restored = ChangeInverse.from_payload(payload)

    assert restored == _inverse()


def test_a_payload_with_no_inverse_reads_as_an_empty_one() -> None:
    """A row written by an older shape must not break a live undo/read."""
    assert ChangeInverse.from_payload({"summary": "s"}) == ChangeInverse()


def test_a_revision_snapshot_survives_a_lesson_that_was_never_generated() -> None:
    """``read_passage``/``generated_at``/the check are all optional (Phase 1)."""
    bare = RevisionSnapshot(
        lesson_id="abc",
        title="A title",
        read_passage=None,
        generated_at=None,
        instruction="Do it differently.",
        quick_check=None,
    )

    payload = change_payload(
        operations=[], summary="s", inverse=ChangeInverse(revisions=(bare,))
    )

    assert ChangeInverse.from_payload(payload).revisions == (bare,)


def test_the_revision_snapshot_for_a_lesson_is_findable_by_id() -> None:
    """``services/generation`` reads the old passage back out for the prompt (D7)."""
    payload = change_payload(
        operations=[], summary="s", inverse=ChangeInverse(revisions=(_snapshot(),))
    )

    found = ChangeInverse.from_payload(payload).revision_for(_snapshot().lesson_id)

    assert found is not None
    assert found.read_passage == "The old passage."
    assert ChangeInverse.from_payload(payload).revision_for("nobody") is None
