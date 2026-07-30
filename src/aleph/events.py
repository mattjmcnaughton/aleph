"""Product analytics events (PRD §5.7, TDD §9).

Product events are structured Logfire events emitted from the service / router
layer. They flow through structlog → ``logfire.StructlogProcessor`` (see
``logging.py``, AL-005) → Logfire, landing as **log records** whose fields become
record attributes — the same sink as spans. The §7 success metrics are then
**saved Logfire SQL queries** over these records (``queries/logfire/*.sql``,
mapped in ``docs/metrics.md``); this module is the single emission seam that makes
those metrics computable.

Every event carries an ``account_id`` and a ``workflow`` tag (§12 vocabulary),
plus the ``path_id`` / ``lesson_id`` / ``position_in_path`` that apply to it (PRD
§5.7: each event is "stamped with account, path, lesson, and timestamp" — the
timestamp is the Logfire record's own). Emission is at **info** level. It is a
clean no-op when Logfire has no token (AL-003); the same events still render to
the structlog console/JSON sink.

**"Computable is verified, not assumed" (AL-070).** :data:`EVENT_FIELDS` is the
manifest of the exact attribute set each event emits. ``tests/unit/test_events``
anchors it to real emission (the emitters cannot drift from it), and
``tests/unit/test_metrics_queries`` checks every attribute referenced by a saved
SQL query against it — so a query can never reference a field no event provides.

Ported from habagou's ``events.py`` seam, specialised to Aleph's fourteen
product events (Phase 1's nine, plus the five tutor events of TDD §9). If
Logfire's retention window ever bounds cohort history (TDD §9 accepted risk), the
fallback is a Postgres events table behind this same seam — the swap is additive,
callers are unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    import uuid

# --- event names (the record ``span_name`` the SQL queries filter on) --------- #

ACCOUNT_CREATED = "account_created"
PATH_CREATED = "path_created"
OUTLINE_GENERATED = "outline_generated"
LESSON_GENERATED = "lesson_generated"
LESSON_VIEWED = "lesson_viewed"
QUICK_CHECK_ATTEMPTED = "quick_check_attempted"
LESSON_COMPLETED = "lesson_completed"
PATH_COMPLETED = "path_completed"
PATH_DELETED = "path_deleted"

# Phase 2 — the in-lesson tutor (TDD §9).
TUTOR_CONVERSATION_STARTED = "tutor_conversation_started"
TUTOR_MESSAGE_SENT = "tutor_message_sent"
TUTOR_REPLY_COMPLETED = "tutor_reply_completed"
TUTOR_CHECK_SHOWN = "tutor_check_shown"
TUTOR_CHECK_ANSWERED = "tutor_check_answered"

# --- workflow tags (§12 shared vocabulary: PRD workflow → test → trace) ------- #

_W_FIRST_PATH = "W1"  # new learner, first path, first lesson (the magic moment)
_W_NORTH_STAR = "W3"  # reach the north-star threshold (path progression/completion)
_W_DELETE = "W5"  # delete a path (reset)
_W_QUICK_CHECK = "W6"  # Quick-check Outcome, both branches
_W_REFUSAL = "W7"  # unsafe topic refused gracefully
_W_FAILURE = "W8"  # generation failure is recoverable
_W_TUTOR_TURN = "W9"  # ask about the lesson you're reading (the Phase 2 moment)
_W_TUTOR_CHECK = "W12"  # a Tutor check, which never touches progress
_W_TUTOR_FAILURE = "W14"  # a failed reply is recoverable

# The manifest: the exact attribute set every event emits. Load-bearing — the
# metric-coverage test checks each saved query's attribute references against it,
# and the events unit test anchors it to what the emitters actually log.
EVENT_FIELDS: dict[str, frozenset[str]] = {
    ACCOUNT_CREATED: frozenset({"account_id", "workflow"}),
    PATH_CREATED: frozenset({"account_id", "path_id", "path_level", "workflow"}),
    OUTLINE_GENERATED: frozenset(
        {
            "account_id",
            "path_id",
            "outcome",
            "success",
            "duration_ms",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "workflow",
        }
    ),
    LESSON_GENERATED: frozenset(
        {
            "account_id",
            "path_id",
            "lesson_id",
            "position_in_path",
            "outcome",
            "success",
            "duration_ms",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "workflow",
        }
    ),
    LESSON_VIEWED: frozenset(
        {"account_id", "path_id", "lesson_id", "position_in_path", "workflow"}
    ),
    QUICK_CHECK_ATTEMPTED: frozenset(
        {
            "account_id",
            "path_id",
            "lesson_id",
            "position_in_path",
            "outcome",
            "is_correct",
            "workflow",
        }
    ),
    LESSON_COMPLETED: frozenset(
        {"account_id", "path_id", "lesson_id", "position_in_path", "workflow"}
    ),
    PATH_COMPLETED: frozenset({"account_id", "path_id", "lesson_count", "workflow"}),
    PATH_DELETED: frozenset({"account_id", "path_id", "workflow"}),
    TUTOR_CONVERSATION_STARTED: frozenset(
        {"account_id", "path_id", "lesson_id", "position_in_path", "workflow"}
    ),
    TUTOR_MESSAGE_SENT: frozenset(
        {"account_id", "path_id", "lesson_id", "position_in_path", "source", "workflow"}
    ),
    TUTOR_REPLY_COMPLETED: frozenset(
        {
            "account_id",
            "path_id",
            "lesson_id",
            "position_in_path",
            "outcome",
            "success",
            "ttft_ms",
            "duration_ms",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "workflow",
        }
    ),
    TUTOR_CHECK_SHOWN: frozenset(
        {"account_id", "path_id", "lesson_id", "position_in_path", "workflow"}
    ),
    TUTOR_CHECK_ANSWERED: frozenset(
        {
            "account_id",
            "path_id",
            "lesson_id",
            "position_in_path",
            "outcome",
            "is_correct",
            "first_answer",
            "workflow",
        }
    ),
}


def _emit(event: str, **fields: object) -> None:
    """Emit one product event as a structured info-level structlog record.

    A fresh ``get_logger`` per call (habagou parity) so the bound logger always
    reflects the current structlog config — cache-safe if a filtering logger was
    cached under a different pipeline (e.g. between ``configure_logging`` calls in
    tests).
    """
    structlog.get_logger("aleph.events").info(event, **fields)


# --- account -----------------------------------------------------------------  #


def emit_account_created(*, account_id: uuid.UUID) -> None:
    """A new account was provisioned (W1) — the north-star cohort anchor.

    Emitted only on first provision (never on returning-learner sign-in), so its
    record timestamp is the account's signup time: the denominator and 7-day
    window origin for the activation-rate and first-lesson-activation metrics.
    """
    _emit(ACCOUNT_CREATED, account_id=str(account_id), workflow=_W_FIRST_PATH)


# --- path lifecycle ----------------------------------------------------------- #


def emit_path_created(
    *, account_id: uuid.UUID, path_id: uuid.UUID, path_level: str
) -> None:
    """A learner created a path (W1) — the path-count source for breadth (§7).

    The onboarding Level is emitted as ``path_level``, **not** ``level``:
    structlog's ``add_log_level`` processor owns the ``level`` key (the log
    severity) and would clobber it, so the domain Level rides a distinct name."""
    _emit(
        PATH_CREATED,
        account_id=str(account_id),
        path_id=str(path_id),
        path_level=path_level,
        workflow=_W_FIRST_PATH,
    )


def emit_path_completed(
    *, account_id: uuid.UUID, path_id: uuid.UUID, lesson_count: int
) -> None:
    """The last lesson of the last unit was completed (W3, PRD §5.4)."""
    _emit(
        PATH_COMPLETED,
        account_id=str(account_id),
        path_id=str(path_id),
        lesson_count=lesson_count,
        workflow=_W_NORTH_STAR,
    )


def emit_path_deleted(*, account_id: uuid.UUID, path_id: uuid.UUID) -> None:
    """A path was deleted (W5, reset). Analytics history lives only in Logfire."""
    _emit(
        PATH_DELETED,
        account_id=str(account_id),
        path_id=str(path_id),
        workflow=_W_DELETE,
    )


# --- generation (success / failure / latency, PRD §5.7) ----------------------- #

_OUTLINE_WORKFLOW = {
    "ready": _W_FIRST_PATH,
    "failed": _W_FAILURE,
    "refused": _W_REFUSAL,
}


def emit_outline_generated(
    *,
    account_id: uuid.UUID,
    path_id: uuid.UUID,
    outcome: str,
    duration_ms: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> None:
    """Outline generation resolved (PRD §5.7: success/failure/latency).

    ``outcome`` is one of ``ready`` (W1), ``failed`` (W8), ``refused`` (W7) — the
    three fenced terminal branches (TDD §5.5). ``success`` is the boolean form for
    the failure-rate guardrail; ``duration_ms`` is the latency; the token counts
    feed the per-path cost query (a token-based proxy — dollar cost also lives on
    the pydantic-ai model-call spans, TDD §10). Emitted only on a **fenced-win**
    mark, never a lost claim, so a path's outline resolution is recorded once.
    """
    _emit(
        OUTLINE_GENERATED,
        account_id=str(account_id),
        path_id=str(path_id),
        outcome=outcome,
        success=outcome == "ready",
        duration_ms=duration_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        workflow=_OUTLINE_WORKFLOW.get(outcome, _W_FIRST_PATH),
    )


def emit_lesson_generated(
    *,
    account_id: uuid.UUID,
    path_id: uuid.UUID,
    lesson_id: uuid.UUID,
    position_in_path: int,
    outcome: str,
    duration_ms: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> None:
    """One lesson's on-demand generation resolved (PRD §5.7: success/failure/latency).

    ``outcome`` is ``generated`` (W1) or ``failed`` (W8). Same field shape as the
    outline event plus the lesson locator; emitted only on a fenced-win mark.
    """
    _emit(
        LESSON_GENERATED,
        account_id=str(account_id),
        path_id=str(path_id),
        lesson_id=str(lesson_id),
        position_in_path=position_in_path,
        outcome=outcome,
        success=outcome == "generated",
        duration_ms=duration_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        workflow=_W_FIRST_PATH if outcome == "generated" else _W_FAILURE,
    )


# --- learner progression ------------------------------------------------------ #


def emit_lesson_viewed(
    *,
    account_id: uuid.UUID,
    path_id: uuid.UUID,
    lesson_id: uuid.UUID,
    position_in_path: int,
) -> None:
    """A learner opened a lesson (W1). ``position_in_path`` is what the path-start
    (start lesson 1) and continuation (start lesson N+1) metrics key on (§7)."""
    _emit(
        LESSON_VIEWED,
        account_id=str(account_id),
        path_id=str(path_id),
        lesson_id=str(lesson_id),
        position_in_path=position_in_path,
        workflow=_W_FIRST_PATH,
    )


def emit_quick_check_attempted(
    *,
    account_id: uuid.UUID,
    path_id: uuid.UUID,
    lesson_id: uuid.UUID,
    position_in_path: int,
    outcome: str,
) -> None:
    """A learner attempted a Quick check (W6). ``outcome`` is ``correct`` /
    ``incorrect`` (the Outcome vocabulary); ``is_correct`` is its boolean form for
    the correctness-rate guardrail. The attempt is the gate on "activated": the
    activation metric counts a completed lesson only if it also has this event."""
    _emit(
        QUICK_CHECK_ATTEMPTED,
        account_id=str(account_id),
        path_id=str(path_id),
        lesson_id=str(lesson_id),
        position_in_path=position_in_path,
        outcome=outcome,
        is_correct=outcome == "correct",
        workflow=_W_QUICK_CHECK,
    )


def emit_lesson_completed(
    *,
    account_id: uuid.UUID,
    path_id: uuid.UUID,
    lesson_id: uuid.UUID,
    position_in_path: int,
) -> None:
    """A learner marked a lesson complete (W1). Emitted only on the real
    transition (not an idempotent re-complete), so counts are not inflated.

    With ``quick_check_attempted`` this is the activation signal: >3 such lessons
    (each also attempted) on a single path within 7 days = an activated learner."""
    _emit(
        LESSON_COMPLETED,
        account_id=str(account_id),
        path_id=str(path_id),
        lesson_id=str(lesson_id),
        position_in_path=position_in_path,
        workflow=_W_FIRST_PATH,
    )


# --- the tutor (Phase 2, TDD §9) ---------------------------------------------- #
#
# Five events, all stamped with the full lesson locator, because every §7 tutor
# metric is per-lesson: the primary one compares continuation for lessons *with*
# a tutor message against lessons without, which needs the lesson a message was
# asked in and the position that lesson sits at.


def emit_tutor_conversation_started(
    *,
    account_id: uuid.UUID,
    path_id: uuid.UUID,
    lesson_id: uuid.UUID,
    position_in_path: int,
) -> None:
    """A path's conversation row was created (W9, TDD §9).

    There is one conversation per path (PRD §5.8) and it is created **lazily, on
    the first turn that settles** (TDD §4/D2) — so this fires once per path, and
    only for a turn that actually persisted. The lesson locator is the lesson the
    first question was asked in, which is the "where did the tutor get picked
    up" datum; later turns in other lessons carry their own.
    """
    _emit(
        TUTOR_CONVERSATION_STARTED,
        account_id=str(account_id),
        path_id=str(path_id),
        lesson_id=str(lesson_id),
        position_in_path=position_in_path,
        workflow=_W_TUTOR_TURN,
    )


def emit_tutor_message_sent(
    *,
    account_id: uuid.UUID,
    path_id: uuid.UUID,
    lesson_id: uuid.UUID,
    position_in_path: int,
    source: str,
) -> None:
    """A turn was **admitted** — every pre-stream gate passed (W9, TDD §9).

    Admission, not persistence, is the moment: this is the learner's act, and a
    reply that then fails is a failure of ours, not an un-asked question. It is
    therefore the honest denominator for the reply-failure guardrail and the
    honest "did this learner use the tutor" signal for adoption and for the
    primary metric — both of which would silently exclude the learners the tutor
    failed if the event waited for a settled turn (D2 persists nothing on
    failure). A send refused before admission (409 in-flight, 429 cap, 404/409
    lesson state) emits nothing: it never became a turn.

    ``source`` is ``typed`` or ``suggestion`` (:class:`~aleph.models.MessageSource`)
    — the §7 entry-mix datum that says whether the suggestions do the teaching
    the mock claims they do.
    """
    _emit(
        TUTOR_MESSAGE_SENT,
        account_id=str(account_id),
        path_id=str(path_id),
        lesson_id=str(lesson_id),
        position_in_path=position_in_path,
        source=source,
        workflow=_W_TUTOR_TURN,
    )


_TUTOR_REPLY_WORKFLOW = {
    "success": _W_TUTOR_TURN,
    "failure": _W_TUTOR_FAILURE,
    # A stopped reply is the learner ending their own turn, not a fault: it
    # stays on W9 so the W14 slice remains "things that broke".
    "stopped": _W_TUTOR_TURN,
}


def emit_tutor_reply_completed(
    *,
    account_id: uuid.UUID,
    path_id: uuid.UUID,
    lesson_id: uuid.UUID,
    position_in_path: int,
    outcome: str,
    duration_ms: int,
    ttft_ms: int | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> None:
    """A reply resolved, **however it resolved** (TDD §9, PRD §5.9).

    ``outcome`` is one of ``success`` (W9), ``failure`` (W14) or ``stopped``
    (W9 — the learner aborted, via the rail's stop affordance or by leaving;
    from the server the two are the same event and mean the same thing).
    ``success`` is the boolean form for the failure-rate guardrail. **A refusal
    is a success**: an over-the-boundary ask answered gracefully is a real,
    persisted turn and is deliberately not machine-tagged this phase (D5,
    PRD §5.7b) — the distinction lives in the reply text and the evals.

    This is the only place PRD §5.9's *latency to first token* exists: no
    Phase 1 event has it, because no Phase 1 surface streams. ``ttft_ms`` is the
    milliseconds from the model call opening to the first text delta reaching
    the wire, and it is ``None`` when the reply produced no delta at all (a
    hung provider, an immediate upstream error). ``None`` rather than ``0``
    because a zero is indistinguishable from an instant first token and would
    drag the p95 panel down; ``percentile_cont`` skips NULLs, so the field can
    always be present without ever being a lie.

    ``duration_ms`` is the whole turn as the *learner* feels it, and is
    populated on every outcome: it is clocked from the moment production starts
    — **before** the tutor concurrency permit is acquired — so it includes any
    wait queued behind the semaphore, not only the streaming. So it is not
    comparable with ``ttft_ms`` as "streaming time" (TTFT is clocked from inside
    the permit), and it can exceed ``TUTOR_REPLY_TIMEOUT``, which bounds the
    model run alone. Queue time is real waiting: excluding it would make the
    latency guardrail read healthy exactly when the pool is saturated.

    The token triple is the pydantic-ai run's usage, zero when the run never
    completed. Per-call dollar cost still lives on the model-call spans (TDD
    §10) — PRD §7 adds no tutor cost metric of its own.
    """
    _emit(
        TUTOR_REPLY_COMPLETED,
        account_id=str(account_id),
        path_id=str(path_id),
        lesson_id=str(lesson_id),
        position_in_path=position_in_path,
        outcome=outcome,
        success=outcome == "success",
        ttft_ms=ttft_ms,
        duration_ms=duration_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        workflow=_TUTOR_REPLY_WORKFLOW.get(outcome, _W_TUTOR_TURN),
    )


def emit_tutor_check_shown(
    *,
    account_id: uuid.UUID,
    path_id: uuid.UUID,
    lesson_id: uuid.UUID,
    position_in_path: int,
) -> None:
    """The tutor posed a Tutor check and its card went to the rail (W12, TDD §9).

    Emitted where ``pose_tutor_check`` is **observed** — the moment the accepted
    tool result is translated into the ``tutor_check`` frame, mid-stream, which
    is the moment the learner sees the card. A call the agent's validator
    rejected (``ModelRetry``) posed nothing and emits nothing.

    Mid-stream means a check shown on a reply that then fails is still counted
    as shown, though D2 persisted nothing and the card cannot be answered. That
    is deliberate — it *was* shown — and it cannot bias §7's Tutor-check uptake,
    whose denominator is tutor **users**, not checks shown. See docs/metrics.md.
    """
    _emit(
        TUTOR_CHECK_SHOWN,
        account_id=str(account_id),
        path_id=str(path_id),
        lesson_id=str(lesson_id),
        position_in_path=position_in_path,
        workflow=_W_TUTOR_CHECK,
    )


def emit_tutor_check_answered(
    *,
    account_id: uuid.UUID,
    path_id: uuid.UUID,
    lesson_id: uuid.UUID,
    position_in_path: int,
    outcome: str,
    first_answer: bool,
) -> None:
    """A learner answered a Tutor check (W12). ``outcome`` is ``correct`` /
    ``incorrect`` (the shared Outcome vocabulary), ``is_correct`` its boolean
    form. A Tutor check is non-scoring: this creates no Attempt and feeds no
    Phase 1 metric (PRD §5.5).

    **Re-answers re-emit, tagged ``first_answer=False``** (the decision AL-221
    left open when it made the write last-wins). The Quick check's opposite rule
    — emit only the first-wins Attempt — exists because a repeat submit there
    writes *nothing*; here a re-answer genuinely rewrites the stored payload, so
    an event per real state change is what keeps this seam an honest record of
    what happened, and suppressing it would make "the learner cycled three
    options before settling" unrecoverable — a signal about the *check's*
    quality that nothing else captures.

    ``first_answer`` is what keeps §7 honest over that choice. Tutor-check
    uptake counts **distinct accounts**, so re-answers cannot inflate it either
    way; any per-check rate (correctness, shown→answered) filters
    ``first_answer = 'true'`` so a learner cycling options cannot move it. See
    ``queries/logfire/tutor_check_uptake.sql`` and docs/metrics.md.
    """
    _emit(
        TUTOR_CHECK_ANSWERED,
        account_id=str(account_id),
        path_id=str(path_id),
        lesson_id=str(lesson_id),
        position_in_path=position_in_path,
        outcome=outcome,
        is_correct=outcome == "correct",
        first_answer=first_answer,
        workflow=_W_TUTOR_CHECK,
    )
