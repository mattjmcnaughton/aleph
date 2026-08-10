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
nothing about their math (AL-240). Phase 2B adds a third: **shaping yield**,
whose join runs through a JSON-encoded list of lesson ids and would silently
return zero rather than raise if that decoding were wrong (AL-340). A final pass
smoke-executes every remaining query so the whole set is proven to parse and run
on the real dialect (the risk AL-103 inherits when importing these to Logfire).

**The shaping fixture deliberately hangs off account B, not A.** B never
activated (its qualifying work falls outside its 7-day window), and every
asserted Phase 1/2A query is either scoped to the activated set or to
`account_created`, so B's shaping traffic — including the lesson views and
completions the hoarding guardrail needs — cannot move a single number those
tests pin. Hanging it off A would have changed the primary tutor metric's
denominators, which would mean editing 2A's assertions to land a 2B ticket.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path as FsPath

import pytest
from sqlalchemy import text

from aleph import db, events

_QUERIES_DIR = FsPath(__file__).resolve().parents[2] / "queries" / "logfire"

# Two matured accounts (signed up 30 days ago, so past the cohort clamp). A does
# its activating work INSIDE its 7-day window; B does the identical work OUTSIDE
# it — so only A is an activated learner.
_SIGNUP = datetime.now(UTC) - timedelta(days=30)
_IN_WINDOW = _SIGNUP + timedelta(days=1)
_OUT_WINDOW = _SIGNUP + timedelta(days=10)
_ACCOUNT_A = str(uuid.uuid4())
_ACCOUNT_B = str(uuid.uuid4())
_ACCOUNT_C = str(uuid.uuid4())

# A's four activating lessons on path-a1, addressable so the tutor fixtures can
# hang off a *named* one: the primary metric splits lessons with tutor use from
# lessons without, so the split is only real if both sides are identifiable.
_A_LESSONS = [str(uuid.uuid4()) for _ in range(4)]
_TUTOR_LESSON = _A_LESSONS[1]  # position 2 — the one lesson A asked about

# B's shaping, on a second path of its own. The timestamps sit far enough in the
# past that shaping_yield's maturity clamp (applied > 7 days ago) admits them,
# and the engagement lands one day after the apply, inside the 7-day window it
# is measured against.
_SHAPED_AT = _OUT_WINDOW
_ENGAGED_AT = _OUT_WINDOW + timedelta(days=1)
_MINUTE = timedelta(minutes=1)
# Two added lessons and one revised one. The first added lesson is engaged with
# (that Change yields); the revised one never is (that Change does not).
_ADDED_LESSONS = [str(uuid.uuid4()) for _ in range(2)]
_REVISED_LESSON = str(uuid.uuid4())
_CHANGE_ADD = str(uuid.uuid4())
_CHANGE_REVISE = str(uuid.uuid4())

# C's analyst (Phase 6, AL-540): a separate account, on the same "own account,
# own math" precedent B's shaping hangs off — nothing here can move a Phase
# 1/2A/2B/3 assertion. Two Beats and a mix of outcomes, sized so the funnel
# columns in ``brief_skip_rate.sql`` have real, DIFFERENT averages to read:
# BEAT_1's skip is healthy-documents/low-findings (retrieval PRECISION),
# BEAT_2's is low-documents/low-findings (retrieval RECALL).
_BEAT_DEPLOYED_AT = _OUT_WINDOW
_BEAT_1 = str(uuid.uuid4())
_BEAT_2 = str(uuid.uuid4())
_BRIEF_1 = str(uuid.uuid4())
_RESEARCH_AT = _BEAT_DEPLOYED_AT + timedelta(minutes=1)
_OPENED_AT = _RESEARCH_AT + timedelta(minutes=2)  # inside wait-tolerance's window
_SOURCES_SEEN_AT = _OPENED_AT + timedelta(minutes=1)


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
        *_shaping_records(),
        *_analyst_records(),
    ]
    return rows


def _shaping_records() -> list[dict]:
    """B's shaping of a second path, ``path-b2`` (Phase 2B, AL-340).

    Sized so every §7 shaping query computes a number that could not have come
    out of an empty slice:

    * **four** admitted messages, staggered a minute apart, and four reply
      resolutions — three successes and one failure carrying the JSON-text
      ``null`` TTFT, so the latency guardrail has both branches;
    * **three** Proposals shown against **two** Changes applied, so proposal
      acceptance is 2/3 rather than a trivial 1;
    * the first Proposal lands on the *second* message, so depth-to-proposal is
      2 — a number that can only be right if the ``<= shown_at`` bound and the
      "count the ask that produced it" rule are both implemented;
    * one Change **undone**, so the undo rate is 1/2 and the time-to-undo
      percentile has a fractional value to read;
    * one added lesson **engaged with**, the revised one not, so the primary
      metric's yield is 1/2 with both sides real.

    ``lesson_ids`` is written as **JSON text**, not as a nested array: that is
    exactly how Logfire stores a list attribute (it serialises on the way in),
    and reproducing it faithfully is what makes the query's
    ``(attributes ->> 'lesson_ids')::jsonb`` cast a tested claim rather than a
    guess — the same fidelity the ``ttft_ms='null'`` quirk gets above.
    """
    conversation = {"account_id": _ACCOUNT_B, "path_id": "path-b2"}
    replied = {"prompt_tokens": 400, "completion_tokens": 120, "total_tokens": 520}
    return [
        # B's path became shapeable — the shaping-adoption denominator. A's
        # path-a1 outline above is the other half of it, and A never shaped, so
        # adoption is 1 of 2.
        _rec(
            "outline_generated",
            _SIGNUP,
            account_id=_ACCOUNT_B,
            path_id="path-b2",
            outcome="ready",
            success=True,
            duration_ms=1100,
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        ),
        _rec("shaping_conversation_started", _SHAPED_AT, **conversation),
        _rec("shaping_message_sent", _SHAPED_AT, **conversation, source="typed"),
        _rec(
            "shaping_reply_completed",
            _SHAPED_AT,
            **conversation,
            **replied,
            outcome="success",
            success=True,
            ttft_ms=120,
            duration_ms=1500,
            has_proposal=False,
        ),
        # The second ask is the one that produces a card — so two messages had
        # been sent by the time the first Proposal was shown.
        _rec(
            "shaping_message_sent",
            _SHAPED_AT + _MINUTE,
            **conversation,
            source="suggestion",
        ),
        _rec(
            "proposal_shown",
            _SHAPED_AT + _MINUTE,
            **conversation,
            n_add_lessons=2,
            n_revisions=0,
            new_unit=False,
        ),
        _rec(
            "shaping_reply_completed",
            _SHAPED_AT + _MINUTE,
            **conversation,
            **replied,
            outcome="success",
            success=True,
            ttft_ms=120,
            duration_ms=1500,
            has_proposal=True,
        ),
        # A card shown on a reply that then failed: still shown (it reached the
        # rail), never appliable (D2 persisted nothing).
        _rec(
            "shaping_message_sent",
            _SHAPED_AT + 2 * _MINUTE,
            **conversation,
            source="typed",
        ),
        _rec(
            "proposal_shown",
            _SHAPED_AT + 2 * _MINUTE,
            **conversation,
            n_add_lessons=1,
            n_revisions=0,
            new_unit=True,
        ),
        _rec(
            "shaping_reply_completed",
            _SHAPED_AT + 2 * _MINUTE,
            **conversation,
            outcome="failure",
            success=False,
            # The OTEL quirk, spelled exactly as logfire carries it.
            ttft_ms="null",
            duration_ms=30000,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            has_proposal=True,
        ),
        _rec(
            "shaping_message_sent",
            _SHAPED_AT + 3 * _MINUTE,
            **conversation,
            source="typed",
        ),
        _rec(
            "proposal_shown",
            _SHAPED_AT + 3 * _MINUTE,
            **conversation,
            n_add_lessons=0,
            n_revisions=1,
            new_unit=False,
        ),
        _rec(
            "shaping_reply_completed",
            _SHAPED_AT + 3 * _MINUTE,
            **conversation,
            **replied,
            outcome="success",
            success=True,
            ttft_ms=120,
            duration_ms=1500,
            has_proposal=True,
        ),
        _rec(
            "change_applied",
            _SHAPED_AT + 5 * _MINUTE,
            **conversation,
            change_id=_CHANGE_ADD,
            n_add_lessons=2,
            n_revisions=0,
            new_unit=False,
            lesson_ids=json.dumps(_ADDED_LESSONS),
        ),
        _rec(
            "change_applied",
            _SHAPED_AT + 6 * _MINUTE,
            **conversation,
            change_id=_CHANGE_REVISE,
            n_add_lessons=0,
            n_revisions=1,
            new_unit=False,
            lesson_ids=json.dumps([_REVISED_LESSON]),
        ),
        _rec(
            "change_undone",
            _SHAPED_AT + 9 * _MINUTE,
            **conversation,
            change_id=_CHANGE_REVISE,
            minutes_since_apply=3.0,
        ),
        # The yield: B comes back the next day and works on ONE of the two
        # lessons the addition created. Nothing ever touches the revised one.
        _rec(
            "lesson_viewed",
            _ENGAGED_AT,
            account_id=_ACCOUNT_B,
            path_id="path-b2",
            lesson_id=_ADDED_LESSONS[0],
            position_in_path=4,
        ),
        _rec(
            "lesson_viewed",
            _ENGAGED_AT,
            account_id=_ACCOUNT_B,
            path_id="path-b2",
            lesson_id=_ADDED_LESSONS[1],
            position_in_path=5,
        ),
        _rec(
            "quick_check_attempted",
            _ENGAGED_AT,
            account_id=_ACCOUNT_B,
            path_id="path-b2",
            lesson_id=_ADDED_LESSONS[0],
            position_in_path=4,
            outcome="correct",
            is_correct=True,
        ),
        _rec(
            "lesson_completed",
            _ENGAGED_AT,
            account_id=_ACCOUNT_B,
            path_id="path-b2",
            lesson_id=_ADDED_LESSONS[0],
            position_in_path=4,
        ),
    ]


def _analyst_records() -> list[dict]:
    """C's Beats (Phase 6, AL-540) — two deployments, four research runs
    across a mix of outcomes, and two read pings on the one published Brief.

    Sized so every §7 analyst query computes a number that could not have
    come out of an empty slice:

    * **two** ``beat_deployed`` (the deployment-mix denominator);
    * BEAT_1: one **published** run (real funnel numbers, two documents
      surviving retrieve()'s own filters) followed by one **skipped** run
      whose documents stayed healthy but findings did not — retrieval
      PRECISION's own signature (TDD §15);
    * BEAT_2: one **skipped** run whose documents were already thin —
      retrieval RECALL's signature — and one **failed** run (retrieval
      never returned anything at all), so ``skip_rate`` has a real
      denominator beyond just the skipped rows;
    * BEAT_1's published Brief is opened, then its Sources are reached, both
      well inside ``brief_wait_tolerance.sql``'s presence window — so
      ``brief_read_rate``, ``brief_depth_of_read``, ``brief_wait_tolerance``
      and ``cost_per_read_brief`` all have one genuinely "present and read"
      row to compute from, and one activity day before + one on/after C's
      first deployment gives ``brief_return.sql`` both halves of its split.
    """
    return [
        _rec(
            "beat_deployed",
            _BEAT_DEPLOYED_AT,
            account_id=_ACCOUNT_C,
            beat_id=_BEAT_1,
            beat_level="some_experience",
            anchor_weekday=0,
            has_guidance=True,
        ),
        _rec(
            "beat_deployed",
            _BEAT_DEPLOYED_AT,
            account_id=_ACCOUNT_C,
            beat_id=_BEAT_2,
            beat_level="new_to_it",
            anchor_weekday=2,
            has_guidance=False,
        ),
        # BEAT_1 run 1: published, healthy documents and findings.
        _rec(
            "brief_research_completed",
            _RESEARCH_AT,
            account_id=_ACCOUNT_C,
            beat_id=_BEAT_1,
            outcome="published",
            duration_ms=42_000,
            queries=6,
            documents_retrieved=10,
            documents_after_filters=8,
            findings=5,
            survivors=2,
            prompt_tokens=900,
            completion_tokens=300,
            total_tokens=1200,
        ),
        # BEAT_1 run 2 (a week later): skipped, but documents stayed
        # healthy — retrieval PRECISION's signature, not recall's.
        _rec(
            "brief_research_completed",
            _RESEARCH_AT + timedelta(days=7),
            account_id=_ACCOUNT_C,
            beat_id=_BEAT_1,
            outcome="skipped",
            duration_ms=15_000,
            queries=6,
            documents_retrieved=9,
            documents_after_filters=7,
            findings=1,
            survivors=0,
            prompt_tokens=400,
            completion_tokens=100,
            total_tokens=500,
        ),
        # BEAT_2 run 1: skipped, and documents were ALREADY thin —
        # retrieval RECALL's signature.
        _rec(
            "brief_research_completed",
            _RESEARCH_AT,
            account_id=_ACCOUNT_C,
            beat_id=_BEAT_2,
            outcome="skipped",
            duration_ms=8_000,
            queries=6,
            documents_retrieved=1,
            documents_after_filters=1,
            findings=0,
            survivors=0,
            prompt_tokens=150,
            completion_tokens=40,
            total_tokens=190,
        ),
        # BEAT_2 run 2: failed before the researcher was ever called —
        # findings/survivors/tokens are honestly 0, never a placeholder.
        _rec(
            "brief_research_completed",
            _RESEARCH_AT + timedelta(days=7),
            account_id=_ACCOUNT_C,
            beat_id=_BEAT_2,
            outcome="failed",
            duration_ms=500,
            queries=6,
            documents_retrieved=0,
            documents_after_filters=0,
            findings=0,
            survivors=0,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        ),
        # BEAT_1's published Brief: opened almost immediately (still
        # present), then its Sources reached.
        _rec(
            "brief_read",
            _OPENED_AT,
            account_id=_ACCOUNT_C,
            beat_id=_BEAT_1,
            brief_id=_BRIEF_1,
            marker="opened",
            age_days=0,
        ),
        _rec(
            "brief_read",
            _SOURCES_SEEN_AT,
            account_id=_ACCOUNT_C,
            beat_id=_BEAT_1,
            brief_id=_BRIEF_1,
            marker="sources",
            age_days=0,
        ),
        # ONE Active day before the first deployment — brief_return.sql's
        # pre-Beat baseline is honestly NOT a return (only one day).
        _rec(
            "lesson_viewed",
            _BEAT_DEPLOYED_AT - timedelta(days=3),
            account_id=_ACCOUNT_C,
            path_id="path-c1",
            lesson_id=str(uuid.uuid4()),
            position_in_path=1,
        ),
        # TWO Active days on/after the first deployment — post-Beat IS a
        # return, and (FIX 5, code-review) it is a GENUINE one: the
        # Active-day vocabulary that decides membership is held fixed to
        # lesson_viewed/lesson_completed/quick_check_attempted on both sides
        # of the split, so this pair of days is real lesson activity, not
        # brief_read padding a day count the pre-Beat side could never have
        # matched (brief_read cannot occur before a Beat exists at all).
        #
        # Day 1 (deployment day): a lesson_viewed AFTER the brief_read pings
        # above, so the day is genuinely Active under the fixed vocabulary
        # while its FIRST same-day event is still the Brief open — the one
        # brief_first_share counts.
        _rec(
            "lesson_viewed",
            _SOURCES_SEEN_AT + timedelta(minutes=2),
            account_id=_ACCOUNT_C,
            path_id="path-c1",
            lesson_id=str(uuid.uuid4()),
            position_in_path=1,
        ),
        # Day 2: an ordinary lesson_viewed, whose day was never touched by a
        # Brief at all — so brief_first_share is a real 1-of-2, not a
        # trivial 100%.
        _rec(
            "lesson_viewed",
            _OPENED_AT + timedelta(days=1),
            account_id=_ACCOUNT_C,
            path_id="path-c1",
            lesson_id=str(uuid.uuid4()),
            position_in_path=1,
        ),
    ]


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
@pytest.mark.workflow("W17")
async def test_the_primary_shaping_metric_separates_yield_from_hoarding() -> None:
    """Phase 2B's primary metric, executed — two applied Changes, one engaged.

    ``shaping_yield.sql`` joins through a **JSON-encoded list of lesson ids**,
    which is the one thing about it that could be wrong and still run: a decoding
    mistake returns a clean ``0.0`` rather than an error, and the panel would
    read "learners never touch what they ask for" forever. So the fixture makes
    the two sides different — the addition's lesson is worked on the next day,
    the revision's target never is — and the assertion pins ``0.5``, which is
    reachable only if the array really was unnested and matched by id.

    The maturity clamp is exercised implicitly: both Changes are ~20 days old, so
    a clamp that dropped them (or one that was never applied) moves the count off
    2 in one direction or the other.
    """
    async with db.async_session() as session:
        await _load_records(session)

        [row] = await _rows(session, "shaping_yield.sql")

    assert row["changes_applied"] == 2, "both applied Changes must be in the cohort"
    assert row["yield_rate"] == pytest.approx(0.5)


@pytest.mark.anyio
async def test_the_supporting_shaping_metrics_compute_real_numbers() -> None:
    """Adoption, acceptance, depth-to-proposal and the edit-shape mix.

    Each of these is a ratio that an empty or half-decoded slice would return as
    NULL, so the assertions are on the arithmetic rather than on "it ran":

    * **adoption** — two accounts have a ready path (A's ``path-a1``, B's
      ``path-b2``); only B ever applied a Change.
    * **acceptance** — three Proposals shown (one of them on a reply that then
      failed, which still counts), two applied.
    * **depth** — the first card arrived on the second ask, and the ask that
      produced it counts, so the median is 2 and not 1 or 3.
    * **mix** — proposed is 3 lessons added + 1 revised across 3 payloads, one
      of which brought a new unit; applied is 2 added + 1 revised across 2.
    """
    async with db.async_session() as session:
        await _load_records(session)

        [adoption] = await _rows(session, "shaping_adoption.sql")
        [acceptance] = await _rows(session, "proposal_acceptance.sql")
        [depth] = await _rows(session, "depth_to_proposal.sql")
        mix = {row["scope"]: row for row in await _rows(session, "edit_shape_mix.sql")}

    assert adoption["learners_with_a_ready_path"] == 2
    assert adoption["learners_who_shaped"] == 1
    assert adoption["adoption_rate"] == pytest.approx(0.5)

    assert (acceptance["proposals_shown"], acceptance["changes_applied"]) == (3, 2)
    assert acceptance["acceptance_rate"] == pytest.approx(2 / 3)

    assert depth["conversations_with_a_proposal"] == 1
    assert depth["median_messages_to_proposal"] == pytest.approx(2.0)

    assert set(mix) == {"proposed", "applied"}
    assert (mix["proposed"]["lessons_added"], mix["proposed"]["lessons_revised"]) == (
        3,
        1,
    )
    assert mix["proposed"]["with_new_unit"] == 1
    assert (mix["applied"]["lessons_added"], mix["applied"]["lessons_revised"]) == (
        2,
        1,
    )
    assert mix["applied"]["with_new_unit"] == 0
    assert mix["applied"]["addition_share"] == pytest.approx(2 / 3)


@pytest.mark.anyio
@pytest.mark.workflow("W19")
async def test_the_undo_guardrail_keeps_its_fractional_regret_latency() -> None:
    """One of two Changes undone, three minutes after it was applied.

    The float matters: ``minutes_since_apply`` is emitted fractional precisely so
    a fast "that is not what I meant" is not rounded to zero, and a query that
    cast it to an integer would pass a rate assertion while destroying the
    distribution the metric is read for.
    """
    async with db.async_session() as session:
        await _load_records(session)

        [row] = await _rows(session, "undo_rate.sql")

    assert (row["changes_applied"], row["changes_undone"]) == (2, 1)
    assert row["undo_rate"] == pytest.approx(0.5)
    assert row["median_minutes_to_undo"] == pytest.approx(3.0)


@pytest.mark.anyio
@pytest.mark.workflow("W21")
async def test_the_shaping_guardrails_compute_both_of_their_sides() -> None:
    """The hoarding split and the reply-latency panel, both with real math.

    The completion guardrail is only a guardrail if **both** sides exist: B's
    ``path-b2`` is shaped (two lessons started, one completed → 0.5) and A's
    ``path-a1`` is not (three started, all three completed → 1.0). The direction
    here is the fixture's arithmetic, not a claim about the product — the point
    is that a shaped/unshaped comparison is computable at all.

    The latency panel is 2A's, and the ``null`` TTFT quirk has to survive it on
    this rail too: four replies, one failure, and the failed reply's JSON-text
    ``null`` skipped by the percentile rather than raising.
    """
    async with db.async_session() as session:
        await _load_records(session)

        guardrail = {
            row["shaped"]: row
            for row in await _rows(session, "shaped_path_completion_guardrail.sql")
        }
        [latency] = await _rows(session, "shaping_reply_failure_latency.sql")

    assert set(guardrail) == {True, False}, "the split must have both sides"
    assert (guardrail[True]["lessons_started"], guardrail[True]["completion_rate"]) == (
        2,
        pytest.approx(0.5),
    )
    assert guardrail[False]["completion_rate"] is not None, (
        "an empty slice proves nothing"
    )

    assert latency["replies"] == 4
    assert latency["failure_rate"] == pytest.approx(0.25)
    assert latency["stopped_rate"] == pytest.approx(0.0)
    assert latency["proposal_rate"] == pytest.approx(0.75)
    # Only the replies that produced a token are in the percentile; the
    # null-TTFT failure is counted in failure_rate instead of dragging it up.
    assert latency["p95_ttft_ms"] == pytest.approx(120.0)
    # Duration is over successes only, so the 30s failure is not in here.
    assert latency["p95_duration_ms"] == pytest.approx(1500.0)


def test_the_beat_deployed_fixture_does_not_drift_from_event_fields() -> None:
    """FIX 7 (code-review, AL-540): this fixture is the one stand-in for real
    emission whose whole job is fidelity — it passed for a while shaping
    ``beat_deployed`` with the clobbered ``level`` key (structlog's
    ``add_log_level`` owns the bare key, `path_level`'s own precedent) purely
    because no query here reads that field, so nothing else in this module
    would ever have caught it. Asserting every fixture attribute key against
    the real manifest (``events.EVENT_FIELDS``) is what keeps this fixture
    from silently drifting from what production actually emits again."""
    beat_deployed_records = [
        row for row in _fixture_records() if row["span_name"] == "beat_deployed"
    ]
    assert beat_deployed_records, "fixture carries no beat_deployed records"
    known_fields = events.EVENT_FIELDS[events.BEAT_DEPLOYED]
    for row in beat_deployed_records:
        keys = set(json.loads(row["attributes"]))
        unknown = keys - known_fields
        assert not unknown, (
            f"beat_deployed fixture record uses unknown fields {sorted(unknown)} "
            f"(known: {sorted(known_fields)})"
        )


@pytest.mark.anyio
async def test_the_analyst_funnel_separates_precision_from_recall() -> None:
    """``brief_skip_rate.sql`` (TDD §15): BEAT_1's Skip carries HEALTHY
    documents but LOW findings (retrieval PRECISION); BEAT_2's Skip carries
    LOW documents too (retrieval RECALL) — the same events, the same query,
    two different readings, exactly what raw Skip rate alone cannot give."""
    async with db.async_session() as session:
        await _load_records(session)
        rows = {
            row["beat_id"]: row for row in await _rows(session, "brief_skip_rate.sql")
        }

    beat_1, beat_2 = rows[_BEAT_1], rows[_BEAT_2]
    assert beat_1["total_runs"] == 2
    assert beat_1["skipped_runs"] == 1
    assert beat_1["skip_rate"] == pytest.approx(0.5)
    assert beat_2["total_runs"] == 2
    assert beat_2["skipped_runs"] == 1
    assert beat_2["skip_rate"] == pytest.approx(0.5)

    # PRECISION: plenty retrieved AND plenty surviving retrieve()'s own
    # filters, little worth reporting -- the disambiguating field (FIX 4,
    # code-review) is what tells this apart from BEAT_2 below.
    assert beat_1["avg_documents_retrieved_when_skipped"] == pytest.approx(9.0)
    assert beat_1["avg_documents_after_filters_when_skipped"] == pytest.approx(7.0)
    assert beat_1["avg_findings_when_skipped"] == pytest.approx(1.0)
    # RECALL: there was little to retrieve in the first place, and
    # retrieve()'s filters had nothing to eat either -- both funnel counts
    # agree, which is what makes it RECALL and not filter-manufactured.
    assert beat_2["avg_documents_retrieved_when_skipped"] == pytest.approx(1.0)
    assert beat_2["avg_documents_after_filters_when_skipped"] == pytest.approx(1.0)
    assert beat_2["avg_findings_when_skipped"] == pytest.approx(0.0)

    # The healthy baseline (FIX 4, code-review): BEAT_1's one published run
    # is a real, self-contained comparator for its own skipped-side averages.
    assert beat_1["avg_documents_retrieved_when_published"] == pytest.approx(10.0)
    assert beat_1["avg_documents_after_filters_when_published"] == pytest.approx(8.0)
    assert beat_1["avg_findings_when_published"] == pytest.approx(5.0)
    assert beat_1["avg_survivors_when_published"] == pytest.approx(2.0)
    # BEAT_2 never published at all -- the exact case a Beat with a 100%
    # skip rate would also hit: no published-side baseline exists to read
    # (NULL, not zero -- there is nothing to average over).
    assert beat_2["avg_documents_retrieved_when_published"] is None
    assert beat_2["avg_findings_when_published"] is None


@pytest.mark.anyio
async def test_the_analyst_read_and_cost_queries_compute_real_numbers() -> None:
    """``brief_read_rate`` / ``brief_depth_of_read`` / ``brief_wait_tolerance``
    / ``cost_per_read_brief`` — all four have one genuinely published, opened,
    and Sources-reached Brief (BEAT_1's) to compute a real answer from. Every
    analyst record in this fixture is ~10-20 days old (``_OUT_WINDOW``), well
    past FIX 6's right-censoring clamps (24h / 5min), so those clamps drop
    nothing here."""
    async with db.async_session() as session:
        await _load_records(session)
        read_rate = await _scalar(session, "brief_read_rate.sql")
        depth = await _scalar(session, "brief_depth_of_read.sql")
        wait = await _scalar(session, "brief_wait_tolerance.sql")
        [cost] = await _rows(session, "cost_per_read_brief.sql")

    assert read_rate == pytest.approx(1.0)  # the one published Brief was opened
    assert depth == pytest.approx(1.0)  # and its Sources were reached too
    # Opened 2 minutes after the run landed — well inside the presence window.
    assert wait == pytest.approx(1.0)
    # FIX 3 (code-review): dollars, split prompt/completion, plus the
    # retrieval-call count — over the one Brief read. Every research run's
    # tokens/queries count in the numerator (all 4 runs, every outcome):
    #   prompt: 900+400+150+0 = 1450, completion: 300+100+40+0 = 440,
    #   retrieval calls (queries): 6*4 = 24,
    #   dollars: 4 runs' (queries*0.005 + documents_retrieved*0.001 +
    #   prompt*0.000003 + completion*0.000015) = 0.15095.
    assert cost["prompt_tokens_per_read_brief"] == pytest.approx(1450.0)
    assert cost["completion_tokens_per_read_brief"] == pytest.approx(440.0)
    assert cost["retrieval_calls_per_read_brief"] == pytest.approx(24.0)
    assert cost["dollars_per_read_brief"] == pytest.approx(0.15095)


@pytest.mark.anyio
async def test_the_analyst_north_star_splits_pre_and_post_beat_return() -> None:
    """``brief_return.sql`` (FIX 5, code-review): C's one pre-Beat Active day
    is honestly NOT a return (only one day); C's two post-Beat Active days
    ARE — and this time genuinely, not manufactured by an asymmetric
    Active-day vocabulary. Both post-Beat days carry a real
    ``lesson_viewed`` (the SAME event kind the pre-Beat day used), so
    ``return_rate_post_beat`` is computed on identical footing to
    ``return_rate_pre_beat`` — deleting every ``brief_read`` record from the
    fixture would not change either return rate at all, only
    ``brief_first_share``, which is exactly the property FIX 5 requires
    (before the fix, the OLD query widened only the post side with
    ``brief_read`` and the assertions below were producible from Brief
    padding alone — no lesson activity required).

    The earlier of the two post-Beat days starts with the opened Brief
    (before that day's own ``lesson_viewed``), so ``brief_first_share`` is a
    real 1-of-2, not a trivial 0 or 100%."""
    async with db.async_session() as session:
        await _load_records(session)
        [row] = await _rows(session, "brief_return.sql")

    assert row["brief_first_share"] == pytest.approx(0.5)
    assert row["return_rate_post_beat"] == pytest.approx(1.0)
    assert row["return_rate_pre_beat"] == pytest.approx(0.0)


@pytest.mark.anyio
async def test_every_saved_query_executes_on_real_postgres() -> None:
    """Smoke: every checked-in query parses and runs (the AL-103 dialect risk)."""
    async with db.async_session() as session:
        await _load_records(session)
        for sql_file in sorted(_QUERIES_DIR.glob("*.sql")):
            # Must not raise; value may legitimately be NULL for sparse fixtures.
            await session.execute(text(_sql(sql_file.name)))
