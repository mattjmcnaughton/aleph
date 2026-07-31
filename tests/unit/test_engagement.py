"""The engagement boundary, derived (Phase 2B TDD D2).

**Engaged** (CONTEXT.md) is the immutability boundary: a lesson with a recorded
Attempt or marked complete. D2 makes it *one* predicate, derived from existing
columns and never stored, so proposal validation, apply and undo cannot drift
apart — these tests pin that predicate and the position boundary derived from
it.

Pure data in, pure data out (the ``domains`` boundary contract): a service maps
rows to :class:`LessonEngagement` before calling in, so nothing here needs a
database.
"""

from __future__ import annotations

from datetime import UTC, datetime

from aleph.domains.engagement import (
    LessonEngagement,
    first_shapeable_position,
    is_engaged,
)

COMPLETED_AT = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def _lesson(
    position: int, *, completed: bool = False, attempted: bool = False
) -> LessonEngagement:
    return LessonEngagement(
        position_in_path=position,
        completed_at=COMPLETED_AT if completed else None,
        has_attempt=attempted,
    )


# --------------------------------------------------------------------------- #
# The predicate (D2)
# --------------------------------------------------------------------------- #


def test_an_untouched_lesson_is_not_engaged() -> None:
    assert is_engaged(_lesson(1)) is False


def test_an_attempted_lesson_is_engaged() -> None:
    """An Attempt on the lesson's Quick check engages it, complete or not."""
    assert is_engaged(_lesson(1, attempted=True)) is True


def test_a_completed_lesson_is_engaged() -> None:
    """``completed_at`` engages it even with no Attempt (the check is non-gating)."""
    assert is_engaged(_lesson(1, completed=True)) is True


def test_attempted_and_completed_is_engaged() -> None:
    assert is_engaged(_lesson(1, completed=True, attempted=True)) is True


def test_engagement_is_or_not_and() -> None:
    """Either signal alone suffices — the two are alternatives, not conjuncts."""
    assert [
        is_engaged(_lesson(1, attempted=True, completed=False)),
        is_engaged(_lesson(2, attempted=False, completed=True)),
    ] == [True, True]


# --------------------------------------------------------------------------- #
# The boundary position (TDD §5.1 ``ShapingCaps.first_shapeable_position``)
# --------------------------------------------------------------------------- #


def test_first_shapeable_position_is_one_on_an_empty_path() -> None:
    assert first_shapeable_position([]) == 1


def test_first_shapeable_position_is_the_first_position_when_nothing_is_engaged() -> (
    None
):
    assert first_shapeable_position([_lesson(1), _lesson(2), _lesson(3)]) == 1


def test_first_shapeable_position_skips_the_engaged_prefix() -> None:
    lessons = [
        _lesson(1, completed=True),
        _lesson(2, attempted=True),
        _lesson(3),
        _lesson(4),
    ]
    assert first_shapeable_position(lessons) == 3


def test_first_shapeable_position_is_past_the_end_when_every_lesson_is_engaged() -> (
    None
):
    """Additions still have somewhere to go: after the last lesson."""
    lessons = [_lesson(1, completed=True), _lesson(2, completed=True)]
    assert first_shapeable_position(lessons) == 3


def test_first_shapeable_position_ignores_input_order() -> None:
    """Positions decide the boundary, not the order the caller happened to pass."""
    lessons = [_lesson(3), _lesson(1, completed=True), _lesson(2, attempted=True)]
    assert first_shapeable_position(lessons) == 3


def test_first_shapeable_position_tolerates_gaps() -> None:
    """Linearity is an ordering, not contiguous integers (as progression, §4)."""
    lessons = [_lesson(1, completed=True), _lesson(5), _lesson(9)]
    assert first_shapeable_position(lessons) == 5


def test_first_shapeable_position_tolerates_gaps_when_all_engaged() -> None:
    lessons = [_lesson(1, completed=True), _lesson(5, attempted=True)]
    assert first_shapeable_position(lessons) == 6


def test_a_later_engaged_lesson_does_not_move_the_boundary_forward() -> None:
    """The *first* non-engaged position is the boundary, engagement gaps and all.

    Engagement is a prefix in practice (progression is linear), but the
    derivation must not quietly assume it: a stray engaged lesson further along
    never re-opens the earlier unengaged slot to insertion.
    """
    lessons = [_lesson(1, completed=True), _lesson(2), _lesson(3, attempted=True)]
    assert first_shapeable_position(lessons) == 2
