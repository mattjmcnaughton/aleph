"""Executed-math replay of the saved metric queries (AL-070, PRD §7).

The unit tests prove each query only *references* emitted fields ("computable");
this proves the SQL actually *runs* on real Postgres and returns the right number
("verified math"). It loads records shaped exactly like the Logfire ``records``
table (``span_name`` / ``attributes`` jsonb / ``start_timestamp``) into a temp
table and executes the checked-in ``.sql`` against it.

The load-bearing assertion pins the **activated-cohort within-7-days-of-signup**
clause (CONTEXT.md "Activated learner"): an account whose qualifying completions
land *outside* its 7-day window must NOT count as activated in any query that
claims to reuse that definition (activation_rate, breadth, return_rate). Phase 2
adds a second: the **primary tutor metric**'s two-row with/without split, which
is only meaningfully executed if the fixture slice actually contains tutor
events — a tutor-free fixture runs all eight tutor queries to NULL and proves
nothing about their math (AL-240). A final pass smoke-executes every remaining
query so the whole set is proven to parse and run on the real dialect (the risk
AL-103 inherits when importing these to Logfire).
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

# A's four activating lessons on path-a1, addressable so the tutor fixtures can
# hang off a *named* one: the primary metric splits lessons with tutor use from
# lessons without, so the split is only real if both sides are identifiable.
_A_LESSONS = [str(uuid.uuid4()) for _ in range(4)]
_TUTOR_LESSON = _A_LESSONS[1]  # position 2 — the one lesson A asked about


def _rec(span_name: str, at: datetime, **attributes: object) -> dict:
    return {"span_name": span_name, "at": at, "attributes": json.dumps(attributes)}


def _activating_path(
    account_id: str, path_id: str, at: datetime, lesson_ids: list[str] | None = None
) -> list[dict]:
    """One path with 4 completed-and-attempted lessons (the >3 activation gate)."""
    rows: list[dict] = []
    ids = lesson_ids or [str(uuid.uuid4()) for _ in range(4)]
    for position, lesson_id in enumerate(ids, start=1):
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
        *_activating_path(_ACCOUNT_A, "path-a1", _IN_WINDOW, _A_LESSONS),
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
            lesson_id=_A_LESSONS[0],
            position_in_path=1,
        ),
        # A starts lessons 2 and 3, which is what makes continuation real: a
        # completion at position N counts as continued only if position N+1 was
        # started. So lessons 1 and 2 continued; 3 and 4 did not.
        _rec(
            "lesson_viewed",
            _IN_WINDOW,
            account_id=_ACCOUNT_A,
            path_id="path-a1",
            lesson_id=_A_LESSONS[1],
            position_in_path=2,
        ),
        _rec(
            "lesson_viewed",
            _IN_WINDOW,
            account_id=_ACCOUNT_A,
            path_id="path-a1",
            lesson_id=_A_LESSONS[2],
            position_in_path=3,
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
            lesson_id=_A_LESSONS[0],
            position_in_path=1,
        ),
        *_tutor_records(),
    ]
    return rows


def _tutor_records() -> list[dict]:
    """A's tutor use, on exactly one of its four lessons (Phase 2, AL-240).

    Without these every tutor query runs against an empty slice and returns
    NULL, which executes but proves no arithmetic. One touched lesson beside
    three untouched ones — all four completed — is the minimum that gives the
    primary metric's with/without split real math on both sides.

    Two sent messages against one lesson (one typed, one suggested), and two
    reply resolutions: one success carrying a TTFT, one failure carrying the
    **JSON text** ``null`` that OTEL turns a ``None`` attribute into. That
    second one is the SQL side of the quirk: it is why the latency query reads
    TTFT through ``nullif(…, 'null')`` and not a bare cast, which would raise.
    """
    turn = {
        "account_id": _ACCOUNT_A,
        "path_id": "path-a1",
        "lesson_id": _TUTOR_LESSON,
        "position_in_path": 2,
    }
    return [
        _rec("tutor_conversation_started", _IN_WINDOW, **turn),
        _rec("tutor_message_sent", _IN_WINDOW, **turn, source="typed"),
        _rec("tutor_message_sent", _IN_WINDOW, **turn, source="suggestion"),
        _rec(
            "tutor_reply_completed",
            _IN_WINDOW,
            **turn,
            outcome="success",
            success=True,
            ttft_ms=120,
            duration_ms=1500,
            prompt_tokens=400,
            completion_tokens=120,
            total_tokens=520,
        ),
        _rec(
            "tutor_reply_completed",
            _IN_WINDOW,
            **turn,
            outcome="failure",
            success=False,
            # The OTEL quirk, spelled exactly as logfire carries it.
            ttft_ms="null",
            duration_ms=30000,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        ),
        _rec("tutor_check_shown", _IN_WINDOW, **turn),
        _rec(
            "tutor_check_answered",
            _IN_WINDOW,
            **turn,
            outcome="correct",
            is_correct=True,
            first_answer=True,
        ),
    ]


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


async def _rows(session, name: str) -> list[dict]:
    """Every row and column, for the queries whose answer is not one number."""
    result = await session.execute(text(_sql(name)))
    return [dict(row) for row in result.mappings()]


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
async def test_the_primary_tutor_metric_computes_both_sides_of_the_split() -> None:
    """Phase 2's primary metric, executed — two rows, both rates real numbers.

    ``tutor_assisted_continuation.sql`` is eight CTEs and a three-way outer
    join; running it to a single NULL would prove only that it parses. So the
    fixture gives account A four completed lessons on ``path-a1`` with tutor
    messages on exactly one of them (position 2), and views on positions 1-3:

    * position 2 (**with tutor**) — completed, position 3 was started → 1/1.
    * positions 1, 3, 4 (**without**) — only position 1 was followed by the
      next lesson starting → 1/3.

    The gap's *direction* (with > without) is the shape the PRD hopes for; the
    assertion pins the arithmetic, not the hope. B contributes nothing: it never
    activated, so a query that dropped the 7-day window would pull B's four
    untouched completions into the ``without`` side and move that denominator
    off 3.
    """
    async with db.async_session() as session:
        await _load_records(session)

        rows = {
            row["with_tutor"]: row
            for row in await _rows(session, "tutor_assisted_continuation.sql")
        }
        assert set(rows) == {True, False}, "the split must have both sides"

        with_tutor, without = rows[True], rows[False]
        assert with_tutor["continuation_rate"] is not None, (
            "an empty slice proves nothing"
        )
        assert (with_tutor["lessons"], with_tutor["continuation_rate"]) == (
            1,
            pytest.approx(1.0),
        )
        assert (without["lessons"], without["continuation_rate"]) == (
            3,
            pytest.approx(1 / 3),
        )
        assert with_tutor["continuation_rate"] > without["continuation_rate"]

        # Adoption is the number the primary gap must be read beside: A is the
        # only activated account and it used the tutor.
        assert await _scalar(session, "tutor_adoption.sql") == pytest.approx(1.0)


@pytest.mark.anyio
async def test_a_null_ttft_reply_survives_the_latency_percentiles() -> None:
    """The OTEL quirk's SQL side: a TTFT of the JSON text ``null`` must not raise.

    Logfire cannot carry a null OTEL attribute, so a reply that produced no
    token arrives with ``ttft_ms`` = the *string* ``'null'``. A bare
    ``(attributes ->> 'ttft_ms')::float`` raises ``invalid input syntax`` on
    that row, which is exactly the failure this fixture reproduces — the
    ``nullif(…, 'null')`` is what turns it into a skipped NULL instead.
    """
    async with db.async_session() as session:
        await _load_records(session)

        [row] = await _rows(session, "tutor_reply_failure_latency.sql")

    assert row["replies"] == 2
    assert row["failure_rate"] == pytest.approx(0.5)
    assert row["stopped_rate"] == pytest.approx(0.0)
    # Only the reply that produced a token is in the TTFT percentile; the
    # null-TTFT failure is counted in failure_rate instead of dragging it up.
    assert row["p95_ttft_ms"] == pytest.approx(120.0)
    # Duration is over successes only, so the 30s timeout is not in here.
    assert row["p95_duration_ms"] == pytest.approx(1500.0)


@pytest.mark.anyio
async def test_every_saved_query_executes_on_real_postgres() -> None:
    """Smoke: every checked-in query parses and runs (the AL-103 dialect risk)."""
    async with db.async_session() as session:
        await _load_records(session)
        for sql_file in sorted(_QUERIES_DIR.glob("*.sql")):
            # Must not raise; value may legitimately be NULL for sparse fixtures.
            await session.execute(text(_sql(sql_file.name)))
