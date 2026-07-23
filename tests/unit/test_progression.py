"""Unit tests for :mod:`aleph.domains.progression` (TDD §4 unlock derivation).

Pure data in, pure data out — no DB, no fakes, no mocks. The domain derives
unlock state from ``position_in_path`` order plus ``completed_at`` alone, exactly
as TDD §4 specifies:

    complete  iff completed_at set
    available iff first incomplete in position_in_path order
    locked    otherwise
"""

from __future__ import annotations

from datetime import UTC, datetime

from aleph.domains.progression import (
    CompletionSummary,
    LessonProgress,
    UnlockState,
    available_index,
    derive_unlock_states,
    next_lesson,
    summarize_completion,
)

_DONE = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _lesson(position: int, *, complete: bool = False) -> LessonProgress:
    return LessonProgress(
        position_in_path=position,
        completed_at=_DONE if complete else None,
    )


# -- LessonProgress.is_complete ------------------------------------------- #


def test_is_complete_true_when_completed_at_set() -> None:
    assert _lesson(0, complete=True).is_complete is True


def test_is_complete_false_when_completed_at_none() -> None:
    assert _lesson(0, complete=False).is_complete is False


# -- derive_unlock_states: shape edges ------------------------------------ #


def test_empty_path_yields_no_states() -> None:
    assert derive_unlock_states([]) == []


def test_single_incomplete_lesson_is_available() -> None:
    assert derive_unlock_states([_lesson(0)]) == [UnlockState.AVAILABLE]


def test_single_complete_lesson_is_complete() -> None:
    assert derive_unlock_states([_lesson(0, complete=True)]) == [UnlockState.COMPLETE]


def test_all_complete_are_all_complete() -> None:
    lessons = [_lesson(i, complete=True) for i in range(4)]
    assert derive_unlock_states(lessons) == [UnlockState.COMPLETE] * 4


def test_none_complete_only_first_is_available() -> None:
    lessons = [_lesson(0), _lesson(1), _lesson(2)]
    assert derive_unlock_states(lessons) == [
        UnlockState.AVAILABLE,
        UnlockState.LOCKED,
        UnlockState.LOCKED,
    ]


def test_typical_progression_prefix_complete() -> None:
    lessons = [
        _lesson(0, complete=True),
        _lesson(1, complete=True),
        _lesson(2),
        _lesson(3),
    ]
    assert derive_unlock_states(lessons) == [
        UnlockState.COMPLETE,
        UnlockState.COMPLETE,
        UnlockState.AVAILABLE,
        UnlockState.LOCKED,
    ]


# -- derive_unlock_states: order independence & linearity ----------------- #


def test_result_aligns_to_input_order_not_position_order() -> None:
    # Input given out of position order; result must re-align to input order,
    # with unlock states decided by position order.
    lessons = [
        _lesson(2),
        _lesson(0, complete=True),
        _lesson(1),
    ]
    assert derive_unlock_states(lessons) == [
        UnlockState.LOCKED,  # position 2
        UnlockState.COMPLETE,  # position 0
        UnlockState.AVAILABLE,  # position 1 (first incomplete)
    ]


def test_non_contiguous_positions_are_tolerated() -> None:
    # Gaps in the integer positions are fine: §4 speaks of an *order*, never of
    # contiguity. First incomplete in order is still well defined.
    lessons = [
        _lesson(0, complete=True),
        _lesson(2),
        _lesson(5),
    ]
    assert derive_unlock_states(lessons) == [
        UnlockState.COMPLETE,
        UnlockState.AVAILABLE,
        UnlockState.LOCKED,
    ]


def test_out_of_order_completion_stays_literal_to_the_rule() -> None:
    # Completion is decided solely by completed_at (TDD §4), independent of
    # whether earlier lessons are complete. A later-complete-but-earlier-incomplete
    # arrangement still renders the completed lesson COMPLETE.
    lessons = [
        _lesson(0, complete=True),
        _lesson(1),
        _lesson(2, complete=True),
    ]
    assert derive_unlock_states(lessons) == [
        UnlockState.COMPLETE,
        UnlockState.AVAILABLE,
        UnlockState.COMPLETE,
    ]


# -- available_index ------------------------------------------------------ #


def test_available_index_is_first_incomplete_by_position() -> None:
    # Input given out of position order; the returned index points at the input
    # row of the first-incomplete-by-position lesson (position 1, at index 2), so
    # a caller can read rows[i] directly.
    lessons = [
        _lesson(2),
        _lesson(0, complete=True),
        _lesson(1),
    ]
    index = available_index(lessons)
    assert index == 2
    assert lessons[2] == _lesson(1)


def test_available_index_none_when_empty() -> None:
    assert available_index([]) is None


def test_available_index_none_when_all_complete() -> None:
    lessons = [_lesson(i, complete=True) for i in range(3)]
    assert available_index(lessons) is None


def test_available_index_tolerates_gaps() -> None:
    lessons = [_lesson(0, complete=True), _lesson(2), _lesson(5)]
    assert available_index(lessons) == 1


# -- next_lesson ---------------------------------------------------------- #


def test_next_lesson_returns_positional_successor() -> None:
    lessons = [_lesson(0), _lesson(1), _lesson(2)]
    assert next_lesson(lessons, after_position=1) == _lesson(2)


def test_next_lesson_none_after_last() -> None:
    lessons = [_lesson(0), _lesson(1)]
    assert next_lesson(lessons, after_position=1) is None


def test_next_lesson_ignores_completion_state() -> None:
    # Navigation is by position only, independent of unlock/complete state.
    lessons = [_lesson(0, complete=True), _lesson(1, complete=True), _lesson(2)]
    assert next_lesson(lessons, after_position=0) == _lesson(1, complete=True)


def test_next_lesson_tolerates_gaps() -> None:
    lessons = [_lesson(0), _lesson(3), _lesson(7)]
    assert next_lesson(lessons, after_position=3) == _lesson(7)


def test_next_lesson_before_first_returns_first() -> None:
    lessons = [_lesson(5), _lesson(9)]
    assert next_lesson(lessons, after_position=0) == _lesson(5)


def test_next_lesson_none_on_empty() -> None:
    assert next_lesson([], after_position=0) is None


# -- completion rollups --------------------------------------------------- #


def test_summarize_completion_counts() -> None:
    lessons = [
        _lesson(0, complete=True),
        _lesson(1, complete=True),
        _lesson(2),
    ]
    assert summarize_completion(lessons) == CompletionSummary(completed=2, total=3)


def test_summary_is_complete_true_when_all_done() -> None:
    lessons = [_lesson(i, complete=True) for i in range(3)]
    assert summarize_completion(lessons).is_complete is True


def test_summary_is_complete_false_when_partial() -> None:
    lessons = [_lesson(0, complete=True), _lesson(1)]
    assert summarize_completion(lessons).is_complete is False


def test_empty_summary_is_not_complete() -> None:
    # An empty path/unit is not a finished journey (PRD §5.4).
    assert summarize_completion([]) == CompletionSummary(completed=0, total=0)
    assert summarize_completion([]).is_complete is False


def test_completion_rollup_over_a_unit_and_a_whole_path() -> None:
    # Unit vs. path completion is the same rollup over a different lesson set:
    # ``.is_complete`` is the boolean test PRD §5.4 calls for, in both roles.
    complete_set = [_lesson(0, complete=True), _lesson(1, complete=True)]
    partial_set = [_lesson(0, complete=True), _lesson(1)]
    assert summarize_completion(complete_set).is_complete is True
    assert summarize_completion(partial_set).is_complete is False
