"""Pure spaced-repetition scheduling: the ladder and the daily queue (TDD D2/§5.1).

Two concerns live in one module because neither is useful without the other,
and each takes exactly the parameters it needs and no others (Phase 5 D2's
discipline: a parameter a function does not need is a parameter that can
drift) — the ladder (:func:`initial_state`, :func:`apply_grade`,
:func:`got_it_interval_days`) never sees a queue, and the daily selection
(:func:`select_daily_queue`) never sees a grade.

Stdlib only, frozen inputs, no ORM, no clock — the ``domains/__init__.py``
contract verbatim. A service maps ``flashcards``/``flashcard_reviews`` rows to
:class:`CardState`/:class:`Candidate` before calling in, and maps the results
back onto rows/DTOs; "today" and "now" are always supplied by the caller, never
read here.

**Ladder semantics**, pinned by ``tests/unit/test_scheduling.py``:

* A rung *r* means "the next interval is ``ladder[r]`` days". :func:`initial_state`
  is rung 0, due ``kept_on + ladder[0]`` — with the default ladder that is
  *tomorrow*, never today; there is no special case for it, it falls out of
  entering at rung 0.
* ``GOT_IT`` promotes: ``rung = min(rung + 1, len(ladder) - 1)``, and
  ``due_on = today + ladder[new_rung]`` — measured from **today**, never from a
  stale ``due_on``, so a card a week overdue does not compound its lateness
  into the schedule. The top rung is a fixed point: a mature card settles at
  the ladder's longest interval rather than growing without bound.
* ``AGAIN`` demotes: ``rung = max(rung - 1, 0)``, and ``due_on = today`` (not
  ``today + ladder[0]``) — what lets a lapse return later the same session
  rather than tomorrow.
* The ladder is always a **parameter**, never a module constant. Config owns
  the numbers (TDD §13); this module owns only the arithmetic, and a short,
  one-rung, or malformed ladder is rejected at ``Settings`` construction, not
  here.

**``select_daily_queue`` semantics:**

* ``len(candidates) <= cap`` -> every candidate is selected; the overdue/random
  split never runs.
* Otherwise: the ``overdue_slots`` most overdue by ``(due_on, card_id)``, then
  ``cap - overdue_slots`` drawn from the rest by ascending
  ``sha256(f"{seed}:{card_id}")`` — a deterministic hash draw, not
  ``random.Random`` (whose ``sample()`` promises nothing across a Python
  version). The random count is **derived**, never a parameter, so the three
  numbers cannot be configured into disagreement.
* The returned order is ``(due_on, card_id)`` over the selected set — most at
  risk first, and deterministic.
* ``satisfied`` is **never read** by the selection (the D3 invariant): it
  exists so a caller can build the counter and the serve order without a
  second pass, and keeping the selection blind to it is what guarantees the
  day's set cannot shrink as the learner works through it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence
    from datetime import date

# The ladder is a tuple of strictly positive day-counts, indexed by rung.
# Always supplied by the caller (config owns the numbers, TDD §13) — never a
# module constant here.
LadderDays = tuple[int, ...]


class Grade(StrEnum):
    """The two-member ladder outcome (TDD §4.6/D2): a third grade is a schema
    change, not a config mistake — there is no "hard"/"easy" here."""

    AGAIN = "again"
    GOT_IT = "got_it"


@dataclass(frozen=True)
class CardState:
    """A card's ladder position: which rung, and when it is next due."""

    rung: int
    due_on: date


@dataclass(frozen=True)
class Candidate:
    """One card's start-of-day scheduling facts, as input to the daily draw.

    ``due_on`` is the value as of the **start of today** (TDD §5.3) — a
    service reconstructs this from ``flashcard_reviews.due_on_before`` when the
    card was already graded today, never from the live, post-grade
    ``flashcards.due_on``. ``satisfied`` (most recent review today was
    ``got_it``) rides along for the caller's counter/serve-order use but is
    never consulted by :func:`select_daily_queue` itself.
    """

    card_id: uuid.UUID
    due_on: date
    satisfied: bool


def initial_state(*, kept_on: date, ladder: LadderDays) -> CardState:
    """A freshly kept card: rung 0, due ``kept_on + ladder[0]`` — never today."""
    return CardState(rung=0, due_on=kept_on + timedelta(days=ladder[0]))


def apply_grade(
    state: CardState, grade: Grade, *, today: date, ladder: LadderDays
) -> CardState:
    """The next :class:`CardState` after grading, per the ladder semantics above."""
    if grade == Grade.GOT_IT:
        new_rung = min(state.rung + 1, len(ladder) - 1)
        return CardState(rung=new_rung, due_on=today + timedelta(days=ladder[new_rung]))
    new_rung = max(state.rung - 1, 0)
    return CardState(rung=new_rung, due_on=today)


def got_it_interval_days(state: CardState, *, ladder: LadderDays) -> int:
    """The interval, in days, a ``GOT_IT`` grade would actually schedule from ``state``.

    This is what the *Got it* button previews (TDD §5.3's ``QueueCardView
    .got_it_interval_days``), so it **must** match what :func:`apply_grade`
    does on ``GOT_IT`` — ``ladder[min(rung + 1, len(ladder) - 1)]`` — not
    ``ladder[rung]``. The two used to disagree: for a rung-2 card on the
    default ladder ``ladder[rung]`` reads 7 while grading actually promotes to
    rung 3 and schedules 14, which is the bug this function's rename fixes
    (previously ``next_interval_days``). Note, for the record, that TDD §6's
    example payload (``"rung": 2, "got_it_interval_days": 7``) is itself
    inconsistent with §5.1's ``apply_grade`` formula — ``apply_grade`` is
    authoritative, and this function is pinned against it directly
    (``tests/unit/test_scheduling.py``), not against the example.
    """
    promoted_rung = min(state.rung + 1, len(ladder) - 1)
    return ladder[promoted_rung]


def select_daily_queue(
    candidates: Sequence[Candidate], *, seed: str, cap: int, overdue_slots: int
) -> tuple[uuid.UUID, ...]:
    """The day's selected card ids, in serve order ``(due_on, card_id)``.

    See the module docstring for the full semantics; ``seed`` is opaque here
    (the caller composes it, typically ``f"{user_id}:{today}"``, TDD §5.3) and
    only ever feeds the hash draw — it plays no role in the overdue arm.
    """
    if len(candidates) <= cap:
        selected = list(candidates)
    else:
        by_urgency = sorted(candidates, key=lambda c: (c.due_on, c.card_id))
        overdue = by_urgency[:overdue_slots]
        overdue_ids = {c.card_id for c in overdue}
        remainder = [c for c in candidates if c.card_id not in overdue_ids]
        # max(..., 0): a misconfigured overdue_slots > cap must not silently
        # flip this negative and truncate by_hash from the wrong end (Settings
        # already rejects that config at startup — this is belt-and-braces).
        random_count = max(cap - overdue_slots, 0)
        # (digest, card_id): the digest alone ties within a session (astronomically
        # unlikely) but only *within* one call; keying on card_id too makes the
        # sort total unconditionally, rather than resting on Python's stable
        # sort preserving `candidates`' own (caller-supplied, unspecified) order.
        by_hash = sorted(
            remainder,
            key=lambda c: (
                hashlib.sha256(f"{seed}:{c.card_id}".encode()).hexdigest(),
                c.card_id,
            ),
        )
        selected = overdue + by_hash[:random_count]

    return tuple(
        candidate.card_id
        for candidate in sorted(selected, key=lambda c: (c.due_on, c.card_id))
    )
