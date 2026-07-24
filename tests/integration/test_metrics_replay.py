"""Executed-math replay of the saved metric queries (AL-070, PRD §7).

The unit tests prove each query only *references* emitted fields ("computable");
this proves the SQL actually *runs* on real Postgres and returns the right number
("verified math"). It loads records shaped exactly like the Logfire ``records``
table (``span_name`` / ``attributes`` jsonb / ``start_timestamp``) into a temp
table and executes the checked-in ``.sql`` against it.

The load-bearing assertion pins the **activated-cohort within-7-days-of-signup**
clause (CONTEXT.md "Activated learner"): an account whose qualifying completions
land *outside* its 7-day window must NOT count as activated in any query that
claims to reuse that definition (activation_rate, breadth, return_rate). A second
pass smoke-executes every remaining query so the whole set is proven to parse and
run on the real dialect (the risk AL-103 inherits when importing these to
Logfire).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path as FsPath

import pytest
from sqlalchemy import text

from aleph import db

_QUERIES_DIR = FsPath(__file__).resolve().parents[2] / "queries" / "logfire"

# Two matured accounts (signed up 30 days ago, so past the cohort clamp). A does
# its activating work INSIDE its 7-day window; B does the identical work OUTSIDE
# it — so only A is an activated learner.
_SIGNUP = datetime.now(UTC) - timedelta(days=30)
_IN_WINDOW = _SIGNUP + timedelta(days=1)
_OUT_WINDOW = _SIGNUP + timedelta(days=10)
_ACCOUNT_A = str(uuid.uuid4())
_ACCOUNT_B = str(uuid.uuid4())


def _rec(span_name: str, at: datetime, **attributes: object) -> dict:
    return {"span_name": span_name, "at": at, "attributes": json.dumps(attributes)}


def _activating_path(account_id: str, path_id: str, at: datetime) -> list[dict]:
    """One path with 4 completed-and-attempted lessons (the >3 activation gate)."""
    rows: list[dict] = []
    for position in range(1, 5):
        lesson_id = str(uuid.uuid4())
        rows.append(
            _rec(
                "lesson_completed",
                at,
                account_id=account_id,
                path_id=path_id,
                lesson_id=lesson_id,
                position_in_path=position,
            )
        )
        rows.append(
            _rec(
                "quick_check_attempted",
                at,
                account_id=account_id,
                path_id=path_id,
                lesson_id=lesson_id,
                position_in_path=position,
                outcome="correct",
                is_correct=True,
            )
        )
    return rows


def _fixture_records() -> list[dict]:
    rows: list[dict] = [
        _rec("account_created", _SIGNUP, account_id=_ACCOUNT_A),
        _rec("account_created", _SIGNUP, account_id=_ACCOUNT_B),
        # A: activates in-window on path A1, and owns a 2nd path (breadth) plus a
        # 2nd active day (return).
        *_activating_path(_ACCOUNT_A, "path-a1", _IN_WINDOW),
        _rec(
            "path_created",
            _SIGNUP,
            account_id=_ACCOUNT_A,
            path_id="path-a1",
            path_level="new_to_it",
        ),
        _rec(
            "path_created",
            _SIGNUP,
            account_id=_ACCOUNT_A,
            path_id="path-a2",
            path_level="new_to_it",
        ),
        _rec(
            "lesson_viewed",
            _IN_WINDOW + timedelta(days=1),
            account_id=_ACCOUNT_A,
            path_id="path-a1",
            lesson_id=str(uuid.uuid4()),
            position_in_path=1,
        ),
        # B: does the exact same activating work, but OUTSIDE its 7-day window.
        *_activating_path(_ACCOUNT_B, "path-b1", _OUT_WINDOW),
        _rec(
            "path_created",
            _SIGNUP,
            account_id=_ACCOUNT_B,
            path_id="path-b1",
            path_level="new_to_it",
        ),
        # A little generation/outline traffic so the smoke queries have data.
        _rec(
            "outline_generated",
            _SIGNUP,
            account_id=_ACCOUNT_A,
            path_id="path-a1",
            outcome="ready",
            success=True,
            duration_ms=1200,
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        ),
        _rec(
            "lesson_generated",
            _SIGNUP,
            account_id=_ACCOUNT_A,
            path_id="path-a1",
            lesson_id=str(uuid.uuid4()),
            position_in_path=1,
            outcome="generated",
            success=True,
            duration_ms=900,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
        ),
        _rec(
            "lesson_viewed",
            _IN_WINDOW,
            account_id=_ACCOUNT_A,
            path_id="path-a1",
            lesson_id=str(uuid.uuid4()),
            position_in_path=1,
        ),
    ]
    return rows


async def _load_records(session) -> None:
    await session.execute(
        text(
            "CREATE TEMP TABLE records ("
            "  span_name text,"
            "  attributes jsonb,"
            "  start_timestamp timestamptz"
            ") ON COMMIT DROP"
        )
    )
    for row in _fixture_records():
        await session.execute(
            text(
                "INSERT INTO records (span_name, attributes, start_timestamp)"
                " VALUES (:span_name, CAST(:attributes AS jsonb), :at)"
            ),
            row,
        )


def _sql(name: str) -> str:
    return (_QUERIES_DIR / name).read_text(encoding="utf-8")


async def _scalar(session, name: str) -> object:
    result = await session.execute(text(_sql(name)))
    return result.scalar()


@pytest.mark.anyio
async def test_activated_cohort_honours_the_7_day_window() -> None:
    """Only A (in-window) is activated; B (out-of-window) must not count anywhere.

    Pins the CONTEXT.md within-7-days clause across every query that reuses the
    "activated" definition. If a query drops the window, B is wrongly activated
    and its denominator/rate shifts — the assertions below go red.
    """
    async with db.async_session() as session:
        await _load_records(session)

        # North star: 1 of 2 accounts activated.
        assert await _scalar(session, "activation_rate.sql") == pytest.approx(0.5)

        # Breadth: activated = {A}; A runs 2 paths → 1.0. If B counted (1 path),
        # this would be 0.5.
        assert await _scalar(session, "breadth.sql") == pytest.approx(1.0)

        # Return: activated = {A}; A is back on a 2nd distinct day → 1.0. If B
        # counted (1 day), this would be 0.5.
        assert await _scalar(session, "return_rate.sql") == pytest.approx(1.0)


@pytest.mark.anyio
async def test_every_saved_query_executes_on_real_postgres() -> None:
    """Smoke: every checked-in query parses and runs (the AL-103 dialect risk)."""
    async with db.async_session() as session:
        await _load_records(session)
        for sql_file in sorted(_QUERIES_DIR.glob("*.sql")):
            # Must not raise; value may legitimately be NULL for sparse fixtures.
            await session.execute(text(_sql(sql_file.name)))
