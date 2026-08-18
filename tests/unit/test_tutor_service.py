"""Unit coverage for the tutor service's pure stream helpers.

The DB- and stream-driven behaviour lives in
``tests/integration/test_tutor_send.py`` against real Postgres; this pins the
one piece that is a plain function of a payload — the Tutor check's option
ordering, which is what stops the tutor's quizzing inheriting the model's
"always B" habit (``domains/answer_order.py``).
"""

from __future__ import annotations

from aleph.dtos.tutor import TutorCheckDTO
from aleph.services.tutor import _ordered_check

CARD = TutorCheckDTO(
    stem="Which reading matches the passage?",
    options=["alpha", "bravo", "charlie", "delta"],
    correct_index=1,
    explanation="**bravo** is what the passage supports.",
)


def test_ordered_check_keeps_the_same_options_and_keyed_answer() -> None:
    ordered = _ordered_check(CARD)
    assert sorted(ordered.options) == sorted(CARD.options)
    assert ordered.options[ordered.correct_index] == CARD.options[CARD.correct_index]


def test_ordered_check_carries_the_rest_of_the_card_through() -> None:
    ordered = _ordered_check(CARD)
    assert ordered.stem == CARD.stem
    assert ordered.explanation == CARD.explanation
    assert ordered.answered_index is None


def test_ordered_check_is_seeded_on_the_stem() -> None:
    """Reproducible from the payload alone — a different stem, a different order.

    (Not a guarantee for any *given* pair of stems; over a handful, orders vary.)
    """
    assert _ordered_check(CARD) == _ordered_check(CARD)
    orders = {
        _ordered_check(CARD.model_copy(update={"stem": f"stem {n}"})).correct_index
        for n in range(8)
    }
    assert len(orders) > 1


def test_ordered_check_does_not_mutate_its_input() -> None:
    _ordered_check(CARD)
    assert CARD.options == ["alpha", "bravo", "charlie", "delta"]
    assert CARD.correct_index == 1
