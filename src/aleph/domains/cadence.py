"""Pure Cadence derivation: when a Beat next opens for research (TDD D4/§5.1).

**Derived, never stored** — there is no ``next_claimable_at`` column (D4). This
module answers "is this Beat's floor open yet" as a pure function of one fact
that already lives on the row set: the date of its **last entry**, of *either*
kind (CONTEXT.md's **Skipped** is an entry exactly as a published Brief is,
D2). A service resolves that fact with ``MAX(briefs.published_on)`` over a
Beat's rows and passes the single value in; this module never sees a row, a
Beat id, or a session.

Stdlib only, frozen inputs, no ORM, no clock — the ``domains/__init__.py``
contract verbatim. ``today`` is always supplied by the caller (the arrival's
``local_today``, TDD D4a/§5.6), never read here.

**``anchor_weekday`` follows Python's convention** — ``date.weekday()``,
Monday ``= 0`` … Sunday ``= 6`` — so a caller can pass a stored anchor straight
through with no translation table at either end.

**Three PRD rules fall out of one comparison (`today >= next_claimable_on(...)`)
rather than being special-cased in code** — this is D4's whole payoff, and the
reason both functions are pinned by ``tests/unit/test_cadence.py`` instead of
narrated only here:

* **A Beat with no entries is claimable immediately** (PRD §3, "the first Brief
  is researched immediately") — the ``None`` case, handled once.
* **A Skipped entry resets the floor exactly as a published one does** (PRD
  §4.6) — this module does not know, and does not ask, which kind
  ``last_entry_on`` came from; the caller's ``MAX(...)`` already erased that
  distinction, which is the point.
* **W32 — a long absence produces one Brief, not a backlog.** A Beat idle for
  six weeks satisfies :func:`is_claimable` exactly once, because
  :func:`next_claimable_on` always returns the *first* Anchor day after
  ``last_entry_on`` — at most seven days later — never a run of dates to catch
  up on. There is no loop here to bound because there is no loop to write.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date

#: Days in a week — named so the modulo arithmetic below reads as what it is.
_DAYS_PER_WEEK = 7


def next_claimable_on(last_entry_on: date | None, anchor_weekday: int) -> date | None:
    """The first Anchor day **strictly after** ``last_entry_on``.

    ``None`` in, ``None`` out (PRD §3's immediate-first-Brief case) —
    :func:`is_claimable` is what turns that into "claimable", not this
    function.

    Strictly after, not on-or-after: a Beat whose last entry landed *on* its
    Anchor day is not claimable again that same day (a Cadence is a floor on
    frequency, CONTEXT.md — "at most one Brief a week" — not a same-day
    re-run). When ``last_entry_on`` already falls on ``anchor_weekday`` the
    modulo below is forced to a full week rather than zero, which is that rule
    stated as arithmetic.
    """
    if last_entry_on is None:
        return None
    days_ahead = (anchor_weekday - last_entry_on.weekday()) % _DAYS_PER_WEEK
    if days_ahead == 0:
        days_ahead = _DAYS_PER_WEEK
    return last_entry_on + timedelta(days=days_ahead)


def is_claimable(
    last_entry_on: date | None, anchor_weekday: int, *, today: date
) -> bool:
    """Whether a Beat may be claimed for research today.

    ``last_entry_on is None or today >= next_claimable_on(last_entry_on,
    anchor_weekday)`` — stated here as a single local variable rather than two
    calls, so a type checker can see ``next_date`` is only ever ``None`` in
    exactly the branch that already short-circuits the comparison; the
    *behavior* is the formula above, unchanged.
    """
    next_date = next_claimable_on(last_entry_on, anchor_weekday)
    return next_date is None or today >= next_date
