"""Unit tests for the pure spaced-repetition scheduler (TDD D2/§5.1, §11's table).

Pure domain — no fakes, no I/O. The bulk of Phase 3's test value lives here
(TDD §11): the ladder's rung arithmetic, and the D3 invariant that the daily
selection never reads ``satisfied``.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, timedelta

import pytest

from aleph.domains.scheduling import (
    Candidate,
    CardState,
    Grade,
    apply_grade,
    got_it_interval_days,
    initial_state,
    select_daily_queue,
)
from aleph.models.enums import FlashcardGrade

LADDER = (1, 3, 7, 14, 30)


def _card_id(n: int) -> uuid.UUID:
    """A stable, ordered UUID per index — deterministic tie-break fixtures."""
    return uuid.UUID(int=n)


# --- initial_state -----------------------------------------------------------


def test_initial_state_is_rung_zero_due_tomorrow_never_today() -> None:
    kept_on = date(2026, 8, 4)
    state = initial_state(kept_on=kept_on, ladder=LADDER)

    assert state.rung == 0
    assert state.due_on == kept_on + timedelta(days=1)
    assert state.due_on != kept_on


def test_initial_state_with_a_longer_first_rung() -> None:
    kept_on = date(2026, 8, 4)
    state = initial_state(kept_on=kept_on, ladder=(5, 10))

    assert state.rung == 0
    assert state.due_on == kept_on + timedelta(days=5)


# --- GOT_IT --------------------------------------------------------------


@pytest.mark.parametrize("start_rung", [0, 1, 2, 3])
def test_got_it_promotes_one_rung_and_sets_due_on_from_today(start_rung: int) -> None:
    today = date(2026, 8, 4)
    state = CardState(rung=start_rung, due_on=today)

    new_state = apply_grade(state, Grade.GOT_IT, today=today, ladder=LADDER)

    expected_rung = start_rung + 1
    assert new_state.rung == expected_rung
    assert new_state.due_on == today + timedelta(days=LADDER[expected_rung])


def test_got_it_at_top_rung_is_a_fixed_point() -> None:
    today = date(2026, 8, 4)
    top_rung = len(LADDER) - 1
    state = CardState(rung=top_rung, due_on=today)

    new_state = apply_grade(state, Grade.GOT_IT, today=today, ladder=LADDER)

    assert new_state.rung == top_rung
    assert new_state.due_on == today + timedelta(days=LADDER[top_rung])


def test_got_it_on_a_card_a_week_overdue_measures_from_today_not_stale_due_on() -> None:
    # The card's due_on was a week ago; the interval must be measured from
    # "today", not compounded on top of the stale value (TDD §5.1).
    today = date(2026, 8, 4)
    stale_due_on = today - timedelta(days=7)
    state = CardState(rung=1, due_on=stale_due_on)

    new_state = apply_grade(state, Grade.GOT_IT, today=today, ladder=LADDER)

    assert new_state.rung == 2
    assert new_state.due_on == today + timedelta(days=LADDER[2])
    assert new_state.due_on != stale_due_on + timedelta(days=LADDER[2])


# --- AGAIN -----------------------------------------------------------------


@pytest.mark.parametrize("start_rung", [0, 1, 2, 4])
def test_again_demotes_one_rung_and_floors_at_zero_due_today(start_rung: int) -> None:
    today = date(2026, 8, 4)
    state = CardState(rung=start_rung, due_on=today)

    new_state = apply_grade(state, Grade.AGAIN, today=today, ladder=LADDER)

    assert new_state.rung == max(start_rung - 1, 0)
    assert new_state.due_on == today


def test_again_from_rung_zero_stays_at_zero_due_today() -> None:
    today = date(2026, 8, 4)
    state = CardState(rung=0, due_on=today)

    new_state = apply_grade(state, Grade.AGAIN, today=today, ladder=LADDER)

    assert new_state.rung == 0
    assert new_state.due_on == today


def test_again_from_top_rung_demotes_due_today() -> None:
    today = date(2026, 8, 4)
    top_rung = len(LADDER) - 1
    state = CardState(rung=top_rung, due_on=today)

    new_state = apply_grade(state, Grade.AGAIN, today=today, ladder=LADDER)

    assert new_state.rung == top_rung - 1
    assert new_state.due_on == today


# --- got_it_interval_days -----------------------------------------------------
#
# The whole point of this function is that it must never drift from what
# apply_grade(GOT_IT) actually schedules (TDD §5.3's "Got it" preview) — so
# every case below is asserted *against apply_grade's own result*, not against
# a hand-computed ladder index, which is exactly the drift review finding #2
# caught (ladder[rung] previews 7 for a rung-2 card while grading schedules 14).


@pytest.mark.parametrize("rung", [0, 1, 2, 3, 4])
def test_got_it_interval_days_matches_what_apply_grade_actually_schedules(
    rung: int,
) -> None:
    today = date(2026, 8, 4)
    state = CardState(rung=rung, due_on=today)

    previewed = got_it_interval_days(state, ladder=LADDER)
    graded = apply_grade(state, Grade.GOT_IT, today=today, ladder=LADDER)

    assert today + timedelta(days=previewed) == graded.due_on


def test_got_it_interval_days_promotes_past_the_current_rung() -> None:
    # The regression case named in the review: a rung-2 card on the default
    # ladder must preview 14 (ladder[3], the promoted rung), never 7
    # (ladder[2], the un-promoted rung next_interval_days used to return).
    state = CardState(rung=2, due_on=date(2026, 8, 4))
    assert got_it_interval_days(state, ladder=LADDER) == 14
    assert got_it_interval_days(state, ladder=LADDER) != LADDER[state.rung]


def test_got_it_interval_days_at_top_rung_is_the_fixed_point() -> None:
    top_rung = len(LADDER) - 1
    state = CardState(rung=top_rung, due_on=date(2026, 8, 4))
    assert got_it_interval_days(state, ladder=LADDER) == LADDER[top_rung]


def test_got_it_interval_days_on_a_single_rung_ladder() -> None:
    state = CardState(rung=0, due_on=date(2026, 8, 4))
    assert got_it_interval_days(state, ladder=(7,)) == 7


# --- Grade / FlashcardGrade parity ---------------------------------------------
#
# The service maps between these two enums (the pure domain's Grade and the
# stored column's FlashcardGrade) on every grade — a guard that they carry
# identical values so that mapping is never silently partial.


def test_grade_and_flashcard_grade_have_identical_values() -> None:
    assert {member.value for member in Grade} == {
        member.value for member in FlashcardGrade
    }


# --- ladder of length 1 -------------------------------------------------------


def test_ladder_of_length_one_promotion_is_a_no_op() -> None:
    today = date(2026, 8, 4)
    state = CardState(rung=0, due_on=today)
    ladder = (7,)

    new_state = apply_grade(state, Grade.GOT_IT, today=today, ladder=ladder)

    assert new_state.rung == 0
    assert new_state.due_on == today + timedelta(days=7)


def test_ladder_of_length_one_demotion_is_a_no_op() -> None:
    today = date(2026, 8, 4)
    state = CardState(rung=0, due_on=today)
    ladder = (7,)

    new_state = apply_grade(state, Grade.AGAIN, today=today, ladder=ladder)

    assert new_state.rung == 0
    assert new_state.due_on == today


def test_ladder_of_length_one_initial_state() -> None:
    kept_on = date(2026, 8, 4)
    state = initial_state(kept_on=kept_on, ladder=(7,))
    assert state.rung == 0
    assert state.due_on == kept_on + timedelta(days=7)


# --- select_daily_queue: cap not exceeded -------------------------------------


def test_all_candidates_selected_when_at_or_under_cap() -> None:
    today = date(2026, 8, 4)
    candidates = [
        Candidate(card_id=_card_id(i), due_on=today, satisfied=False) for i in range(5)
    ]

    selected = select_daily_queue(
        candidates, seed="user:2026-08-04", cap=10, overdue_slots=7
    )

    assert set(selected) == {c.card_id for c in candidates}
    assert len(selected) == 5


def test_exactly_at_cap_selects_all_and_split_never_runs() -> None:
    today = date(2026, 8, 4)
    candidates = [
        Candidate(card_id=_card_id(i), due_on=today, satisfied=False) for i in range(10)
    ]

    selected = select_daily_queue(
        candidates, seed="user:2026-08-04", cap=10, overdue_slots=7
    )

    assert set(selected) == {c.card_id for c in candidates}


# --- select_daily_queue: over cap ---------------------------------------------


def _backlog(n: int, *, today: date) -> list[Candidate]:
    """``n`` candidates with distinct, ascending ``due_on`` values (all overdue)."""
    return [
        Candidate(
            card_id=_card_id(i), due_on=today - timedelta(days=n - i), satisfied=False
        )
        for i in range(n)
    ]


def test_over_cap_selects_the_n_most_overdue_plus_derived_random_count() -> None:
    today = date(2026, 8, 4)
    candidates = _backlog(20, today=today)

    selected = select_daily_queue(
        candidates, seed="user:2026-08-04", cap=10, overdue_slots=7
    )

    assert len(selected) == 10
    most_overdue = sorted(candidates, key=lambda c: (c.due_on, c.card_id))[:7]
    most_overdue_ids = {c.card_id for c in most_overdue}
    assert most_overdue_ids.issubset(set(selected))
    # The remaining 3 are drawn from the rest, not necessarily the next-most-overdue.
    random_ids = set(selected) - most_overdue_ids
    assert len(random_ids) == 3


def test_over_cap_returned_order_is_due_on_then_card_id() -> None:
    today = date(2026, 8, 4)
    candidates = _backlog(20, today=today)

    selected = select_daily_queue(
        candidates, seed="user:2026-08-04", cap=10, overdue_slots=7
    )

    by_id = {c.card_id: c for c in candidates}
    due_ons = [by_id[card_id].due_on for card_id in selected]
    assert due_ons == sorted(due_ons)


def test_over_cap_with_all_due_on_the_same_orders_by_card_id_tiebreak() -> None:
    # Every candidate shares one `due_on` — production's *common* case (a
    # backlog kept on the same day), not the edge case the distinct-due_on
    # fixture above exercises. With every due_on tied, the `card_id` half of
    # the `(due_on, card_id)` tie-break is what pins the queue's order, so this
    # asserts full tuple equality rather than merely "the dates are sorted".
    today = date(2026, 8, 4)
    seed = "user:2026-08-04"
    candidates = [
        Candidate(card_id=_card_id(i), due_on=today, satisfied=False) for i in range(20)
    ]

    selected = select_daily_queue(candidates, seed=seed, cap=10, overdue_slots=7)

    # "Most overdue" ties purely on card_id since every due_on is identical —
    # the 7 lowest ids, ascending, come first.
    expected_overdue = tuple(_card_id(i) for i in range(7))
    assert selected[:7] == expected_overdue

    # The remaining 3 are drawn from the rest by the hash order; the whole
    # selected set is then re-sorted by (due_on, card_id) for the return, which
    # — due_on tied — is a plain ascending card_id sort over the tail too.
    remainder = [c for c in candidates if c.card_id not in set(expected_overdue)]
    by_hash = sorted(
        remainder,
        key=lambda c: (
            hashlib.sha256(f"{seed}:{c.card_id}".encode()).hexdigest(),
            c.card_id,
        ),
    )
    expected_random = tuple(sorted(c.card_id for c in by_hash[:3]))

    assert selected[7:] == expected_random
    assert selected == expected_overdue + expected_random


# --- determinism ---------------------------------------------------------------


def test_repeated_calls_are_identical_not_an_rng() -> None:
    today = date(2026, 8, 4)
    candidates = _backlog(20, today=today)

    first = select_daily_queue(
        candidates, seed="user:2026-08-04", cap=10, overdue_slots=7
    )
    second = select_daily_queue(
        candidates, seed="user:2026-08-04", cap=10, overdue_slots=7
    )

    assert first == second


def test_different_seed_draws_a_different_random_three() -> None:
    today = date(2026, 8, 4)
    candidates = _backlog(20, today=today)

    a = select_daily_queue(
        candidates, seed="user-a:2026-08-04", cap=10, overdue_slots=7
    )
    b = select_daily_queue(
        candidates, seed="user-b:2026-08-04", cap=10, overdue_slots=7
    )

    assert set(a) != set(b)


def test_different_day_redraws_the_random_three() -> None:
    today = date(2026, 8, 4)
    candidates = _backlog(20, today=today)

    day1 = select_daily_queue(
        candidates, seed="user:2026-08-04", cap=10, overdue_slots=7
    )
    day2 = select_daily_queue(
        candidates, seed="user:2026-08-05", cap=10, overdue_slots=7
    )

    assert set(day1) != set(day2)


# --- the D3 invariant: satisfied is never read --------------------------------


@pytest.mark.parametrize(
    "satisfied_indices",
    [
        set(),
        {0},
        {1, 3, 5, 7},
        set(range(20)),
        {2, 4, 6, 8, 10, 12, 14, 16, 18},
    ],
)
def test_satisfied_flipped_on_any_subset_leaves_selection_unchanged(
    satisfied_indices: set[int],
) -> None:
    today = date(2026, 8, 4)
    baseline = _backlog(20, today=today)
    baseline_selection = select_daily_queue(
        baseline, seed="user:2026-08-04", cap=10, overdue_slots=7
    )

    flipped = [
        Candidate(card_id=c.card_id, due_on=c.due_on, satisfied=i in satisfied_indices)
        for i, c in enumerate(baseline)
    ]
    flipped_selection = select_daily_queue(
        flipped, seed="user:2026-08-04", cap=10, overdue_slots=7
    )

    assert flipped_selection == baseline_selection
