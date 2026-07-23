"""Shared SQL fragments for the generation state machine (TDD §5.4).

The claim protocol and every stale-aware read must agree on **one clock**. That
clock is the database: both the claim's ``generation_started_at = now()`` write
and the stale cutoff ``generation_started_at < now() - GENERATION_STALE_AFTER``
are evaluated by Postgres, so multiple app instances (multiple Fly machines)
with skewed wall clocks still serialize correctly through the row the claim
locks. Reads reuse the same cutoff so a reader's "is this stale?" answer matches
what a claim would decide at the same instant.

These are **pure SQL builders** — they take the stale window as a value and
import no config. The stale window is policy the caller owns (the repository
resolves its default from :mod:`aleph.config`; a service, e.g. AL-040, may
inject a different one). Keeping this module config-free preserves the layering
rule (repositories → models) for the one place the claim/read SQL is defined.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import String, and_, case, func, or_
from sqlalchemy import cast as sa_cast

if TYPE_CHECKING:
    from collections.abc import Sequence
    from enum import StrEnum

    from sqlalchemy import ColumnElement, CursorResult, Result
    from sqlalchemy.orm import InstrumentedAttribute


def affected_rows(result: Result[Any]) -> int:
    """Rows touched by an UPDATE/DELETE.

    ``AsyncSession.execute`` is typed ``Result`` but returns a ``CursorResult``
    for DML statements, and only ``CursorResult`` exposes ``rowcount``. This
    cast is the honest way to reach it — the type checker rejects
    ``result.rowcount`` on the declared ``Result`` type, and a ``getattr``
    fallback would hide a real typo behind a default of ``0``.
    """
    return cast("CursorResult[Any]", result).rowcount


def stale_cutoff(stale_after_seconds: float) -> ColumnElement[object]:
    """The database-clock instant before which a ``generating`` row is stale.

    ``now() - stale_after_seconds``, evaluated by Postgres so every claim and
    read share the DB clock. ``make_interval``'s 7th positional argument is
    seconds.
    """
    return func.now() - func.make_interval(0, 0, 0, 0, 0, 0, stale_after_seconds)


def claimable_predicate(
    *,
    state_col: InstrumentedAttribute[object],
    started_at_col: InstrumentedAttribute[object],
    claimable_states: Sequence[StrEnum],
    generating_state: StrEnum,
    stale_after_seconds: float,
) -> ColumnElement[bool]:
    """WHERE clause for "this row is claimable" — defined once (TDD §5.4).

    A row is claimable iff it is in one of ``claimable_states`` (e.g. a
    never-started row, or — for a retry claim — a ``failed`` one), **or** it is
    a ``generating`` row whose ``started_at`` is older than the stale window (a
    crashed run, self-healing). ``generated``/``ready``/``refused`` are terminal
    and never appear in ``claimable_states``, so they are never re-claimed.
    """
    return or_(
        state_col.in_(claimable_states),
        and_(
            state_col == generating_state,
            started_at_col < stale_cutoff(stale_after_seconds),
        ),
    )


def effective_state_case(
    *,
    state_col: InstrumentedAttribute[object],
    started_at_col: InstrumentedAttribute[object],
    generating_state: StrEnum,
    failed_state: StrEnum,
    stale_after_seconds: float,
) -> ColumnElement[str]:
    """SQL for a row's **effective** state: a stale ``generating`` reads as failed.

    The single CASE the stale-aware reads share (per-lesson, per-path, and the
    grouped progress roll-up), so "is this stale?" gets one answer. Both branches
    yield text so the column has one type; callers map the string back to their
    enum.
    """
    return case(
        (
            and_(
                state_col == generating_state,
                started_at_col < stale_cutoff(stale_after_seconds),
            ),
            sa_cast(failed_state.value, String),
        ),
        else_=sa_cast(state_col, String),
    )
