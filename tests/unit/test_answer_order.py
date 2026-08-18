"""Unit tests for :mod:`aleph.domains.answer_order` (MCQ option ordering).

The bug this module exists for: the model keys the correct option to the same
slot — in practice the second one, "B" on the card — lesson after lesson, and
almost never to the last. These tests pin the two properties that fix it: the
permutation preserves the check (same options, same keyed answer), and it is
*independent of which option is correct*, so a perfectly biased generator still
comes out uniform. The distribution test at the bottom is the one that would
have caught the bug.
"""

from __future__ import annotations

from collections import Counter

import pytest

from aleph.domains.answer_order import OrderedOptions, shuffle_options

OPTIONS = ["alpha", "bravo", "charlie", "delta"]


# -- the check survives the re-ordering ------------------------------------ #


def test_returns_the_same_options_re_ordered() -> None:
    ordered = shuffle_options(OPTIONS, 1, seed="lesson-1")
    assert sorted(ordered.options) == sorted(OPTIONS)


def test_correct_index_follows_its_option() -> None:
    """The keyed answer is the same *text* after the shuffle as before it."""
    for correct_index in range(len(OPTIONS)):
        ordered = shuffle_options(OPTIONS, correct_index, seed="lesson-1")
        assert ordered.options[ordered.correct_index] == OPTIONS[correct_index]


def test_three_options_are_supported() -> None:
    # 3-4 options is the band (CONTEXT.md); nothing here assumes four.
    ordered = shuffle_options(["a", "b", "c"], 2, seed="lesson-1")
    assert sorted(ordered.options) == ["a", "b", "c"]
    assert ordered.options[ordered.correct_index] == "c"


def test_single_option_is_a_fixed_point() -> None:
    assert shuffle_options(["only"], 0, seed="s") == OrderedOptions(("only",), 0)


def test_empty_options_pass_through() -> None:
    """Nothing to order, and no range check to fail on a caller with no options."""
    assert shuffle_options([], 0, seed="s") == OrderedOptions((), 0)


# -- determinism ------------------------------------------------------------ #


def test_same_seed_gives_the_same_order() -> None:
    first = shuffle_options(OPTIONS, 1, seed="lesson-1")
    second = shuffle_options(OPTIONS, 1, seed="lesson-1")
    assert first == second


def test_different_seeds_give_different_orders() -> None:
    """Not a guarantee for any *given* pair — but over a handful, orders vary."""
    orders = {shuffle_options(OPTIONS, 1, seed=f"lesson-{n}").options for n in range(8)}
    assert len(orders) > 1


# -- the independence rule: the permutation may not see the answer --------- #


def test_permutation_does_not_depend_on_correct_index() -> None:
    """One seed, one permutation — whichever option happens to be keyed.

    This is the property that makes the served position uniform: if the
    permutation could vary with ``correct_index`` it could reproduce the very
    bias it is here to remove.
    """
    orders = {
        shuffle_options(OPTIONS, correct_index, seed="lesson-1").options
        for correct_index in range(len(OPTIONS))
    }
    assert len(orders) == 1


def test_permutation_does_not_depend_on_option_text() -> None:
    """Rewriting the options does not move them: only the seed picks the order."""
    original = shuffle_options(OPTIONS, 0, seed="lesson-1")
    renamed = shuffle_options(["w", "x", "y", "z"], 0, seed="lesson-1")
    assert [OPTIONS.index(option) for option in original.options] == [
        ["w", "x", "y", "z"].index(option) for option in renamed.options
    ]


# -- the regression: a biased generator comes out spread ------------------- #


@pytest.mark.parametrize("model_keeps_correct_at", [0, 1, 2, 3])
def test_a_perfectly_biased_generator_lands_in_every_slot(
    model_keeps_correct_at: int,
) -> None:
    """Feed 400 checks whose answer is *always* the same slot; count where it ends.

    Before this module, a model that always keyed index 1 produced 400 checks
    answered "B" — the reported bug, and a path a learner can score without
    reading. Every slot must now be reachable, and none may run away with it:
    the bound is deliberately loose (uniform is 25%, this allows 15-40%) so the
    test pins the defect, not the digest's exact arithmetic.
    """
    landed = Counter(
        shuffle_options(
            OPTIONS, model_keeps_correct_at, seed=f"lesson-{n}"
        ).correct_index
        for n in range(400)
    )
    assert set(landed) == {0, 1, 2, 3}, f"some slot never holds the answer: {landed}"
    assert all(60 <= count <= 160 for count in landed.values()), landed


# -- precondition ----------------------------------------------------------- #


@pytest.mark.parametrize("correct_index", [-1, 4, 99])
def test_out_of_range_correct_index_is_rejected(correct_index: int) -> None:
    """A bug in this app, not a model output to tolerate — every caller has
    already run the agent's ``correct_index_in_range`` validator, so silently
    re-keying the check to the wrong answer would be the worse failure."""
    with pytest.raises(ValueError, match="does not address"):
        shuffle_options(OPTIONS, correct_index, seed="lesson-1")
