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

Ported from habagou's ``events.py`` seam, specialised to Aleph's twenty-six
product events (Phase 1's nine, Phase 2A's five tutor events, Phase 2B's six
shaping events, Phase 3's three flashcard events, and Phase 6's three analyst
events — each phase's TDD §9). If Logfire's retention window ever bounds
cohort history (TDD §9 accepted risk), the fallback is a Postgres events
table behind this same seam — the swap is additive, callers are unchanged.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

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

# Phase 2B — shaping (TDD §9). Path-level: no ``lesson_id``, no
# ``position_in_path``. The lesson ids an edit touched ride ``change_applied``
# as a payload-derived field instead, which is what the primary metric joins on.
SHAPING_CONVERSATION_STARTED = "shaping_conversation_started"
SHAPING_MESSAGE_SENT = "shaping_message_sent"
SHAPING_REPLY_COMPLETED = "shaping_reply_completed"
PROPOSAL_SHOWN = "proposal_shown"
CHANGE_APPLIED = "change_applied"
CHANGE_UNDONE = "change_undone"

# Phase 3 — flashcards & spaced repetition (TDD §9). No session events: a
# session started is the first grade of a day and one finished is a grade with
# ``queue_remaining = 0``, both derivable from ``review_graded`` alone (D9's
# "do not add an event you can compute" posture, applied here).
FLASHCARDS_DRAFTED = "flashcards_drafted"
FLASHCARDS_KEPT = "flashcards_kept"
REVIEW_GRADED = "review_graded"

# Phase 6 — the analyst (TDD §9/§15). Three events: the deployment-mix datum,
# one research-run-resolution event carrying the §15 funnel (never a success
# event plus a separate failure event — the ``lesson_generated``/
# ``tutor_reply_completed`` shape, copied here), and the north star's own
# read ping.
BEAT_DEPLOYED = "beat_deployed"
BRIEF_RESEARCH_COMPLETED = "brief_research_completed"
BRIEF_READ = "brief_read"

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
_W_SHAPE_ADD = "W17"  # shape by adding (the Phase 2B moment)
_W_SHAPE_REVISE = "W18"  # shape by revising
_W_SHAPE_UNDO = "W19"  # undo restores exactly
_W_DUE_CARD = "W24"  # finishing a lesson produces a due card (draft -> keep)
_W_QUEUE_HOLDS = "W25"  # the daily queue caps and holds across reload/grading
_W_LAPSE_RETURNS = "W26"  # a lapse (`again`) resurfaces without costing a slot
_W_BEAT_DEPLOYED = "W29"  # deploy an analyst, get a cited Brief (also: read it)
_W_BRIEF_SKIPPED = "W31"  # a quiet period is Skipped, not padded
#
# **W30** ("the second Brief builds on the first") and **W32** ("a long
# absence produces one Brief, not a backlog") tag no event: both are
# properties of a *sequence* of runs (what a Brief cites, how many Anchor
# days a drain collapsed into one report) rather than a single run's own
# outcome, so ``brief_research_completed``'s one-event-per-run shape has no
# single record to hang either on — they are verified by
# ``tests/integration/test_briefing.py`` directly, the W27/W20/W21 posture
# below applied here. **W33** ("retrieval failure is recoverable and never
# uncited") does not get its own tag either: it is one of several causes
# folded into ``brief_research_completed``'s generic ``failed`` outcome,
# which reuses ``_W_FAILURE`` (W8) — the same cross-phase reuse
# ``flashcards_drafted`` already established for "it broke", rather than
# minting a phase-specific tag for one failure cause among several that all
# resolve to the identical outcome value.
#
# **W27** ("a card survives its source lesson") tags no event, on the same
# posture as W20/W21 below: it is a property of the citation degrading
# honestly (D12, ``services/reviews.py::_citation``) rather than a distinct
# thing that happens, so it is verified by the citation view and the
# integration test, not by a machine-tagged record here.
#
# Two 2B workflows deliberately tag no event. **W20** (an out-of-vocabulary edit
# is declined, not improvised) is an ordinary reply — the decline is not
# machine-tagged this phase (D5's posture, PRD §5.7b), so it is a
# ``shaping_reply_completed`` with ``outcome='success'`` and no proposal, exactly
# like a 2A refusal. **W21** (shaping is never on the critical path) is a
# property of what does *not* happen, so it tags the guardrail *queries*
# (TDD §9) rather than any record.

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
    SHAPING_CONVERSATION_STARTED: frozenset({"account_id", "path_id", "workflow"}),
    SHAPING_MESSAGE_SENT: frozenset({"account_id", "path_id", "source", "workflow"}),
    SHAPING_REPLY_COMPLETED: frozenset(
        {
            "account_id",
            "path_id",
            "outcome",
            "success",
            "ttft_ms",
            "duration_ms",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "has_proposal",
            "workflow",
        }
    ),
    PROPOSAL_SHOWN: frozenset(
        {
            "account_id",
            "path_id",
            "n_add_lessons",
            "n_revisions",
            "new_unit",
            "workflow",
        }
    ),
    CHANGE_APPLIED: frozenset(
        {
            "account_id",
            "path_id",
            "change_id",
            "n_add_lessons",
            "n_revisions",
            "new_unit",
            "lesson_ids",
            "workflow",
        }
    ),
    CHANGE_UNDONE: frozenset(
        {
            "account_id",
            "path_id",
            "change_id",
            "minutes_since_apply",
            "workflow",
        }
    ),
    FLASHCARDS_DRAFTED: frozenset(
        {
            "account_id",
            "path_id",
            "lesson_id",
            "position_in_path",
            "drafted_count",
            "outcome",
            "success",
            "duration_ms",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "workflow",
        }
    ),
    FLASHCARDS_KEPT: frozenset(
        {
            "account_id",
            "path_id",
            "lesson_id",
            "drafted_count",
            "kept_count",
            "workflow",
        }
    ),
    REVIEW_GRADED: frozenset(
        {
            "account_id",
            "card_id",
            "path_id",
            "grade",
            "rung_before",
            "queue_size",
            "queue_remaining",
            "workflow",
        }
    ),
    BEAT_DEPLOYED: frozenset(
        {
            "account_id",
            "beat_id",
            "beat_level",
            "anchor_weekday",
            "has_guidance",
            "workflow",
        }
    ),
    BRIEF_RESEARCH_COMPLETED: frozenset(
        {
            "account_id",
            "beat_id",
            "outcome",
            "duration_ms",
            "queries",
            "documents_retrieved",
            "documents_after_filters",
            "findings",
            "survivors",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "workflow",
        }
    ),
    BRIEF_READ: frozenset(
        {
            "account_id",
            "beat_id",
            "brief_id",
            "marker",
            "age_days",
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


def _emit_guarded(event: str, **fields: object) -> None:
    """:func:`_emit`, but a failing sink never propagates into the request path.

    **Phase 2B's emitters, plus Phase 3's two request-path ones**, and the
    asymmetry is deliberate. 2B's stamps sit where a raised exception would do
    real damage: ``change_applied`` and ``change_undone`` fire *after* their
    transaction has committed, so a raising sink would report a landed change
    to the learner as a ``500``; ``shaping_message_sent`` fires while the turn
    holds its one-in-flight reservation, so it would wedge the conversation
    until the process restarted; ``shaping_reply_completed`` fires from a
    ``finally`` and would replace the real outcome. None of those can be paid
    for with a telemetry failure — "the apply worked" is the truth the learner
    is owed.

    ``emit_review_graded`` (``services/reviews.py::grade_card``) and
    ``emit_flashcards_kept`` (``services/flashcard_drafting.py::
    keep_flashcard_drafts``) make the same argument more strongly still: both
    fire **inside an open write transaction**, ahead of the router's own
    ``session.commit()`` (TDD §9's documented structural trade), so a raising
    sink there would turn an already-correct grade or keep into a ``500`` and
    lose it entirely — worse than 2B's case, where the commit had already
    landed. ``emit_flashcards_drafted`` is deliberately **not** routed through
    here: it runs from the background drafting task, with no request and no
    learner-visible write for a raising sink to break.

    2A's emitters are **not** routed through here, though the same argument
    partly applies to them, because changing them is a behaviour change on the
    frozen in-lesson surface (W21). Unifying the two — one guarded ``_emit`` for
    every event — is the post-2B follow-up, and it is additive when it happens.

    Swallowing is right rather than re-raising: the alternative to a lost metric
    record is a lost learner action. The loss is still reported — as a
    traceback on this module's own logger — and that report is itself
    suppressed, because the failure mode being guarded against is "the logging
    pipeline is broken", and a guard that needs the broken thing to work is not
    a guard.
    """
    try:
        _emit(event, **fields)
    except Exception:
        with contextlib.suppress(Exception):
            structlog.get_logger(__name__).exception(
                "product_event_emission_failed", product_event=event
            )


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


# --- shaping (Phase 2B, TDD §9) ----------------------------------------------- #
#
# Six events, none of them carrying a lesson locator: a shaping turn is about the
# path as a whole (PRD §5.1), which is the whole reason it is a second thread.
# What the *operations* touched rides ``change_applied``'s payload-derived
# ``lesson_ids`` — the join key the primary metric (shaping yield) needs to ask
# "did the learner then engage with what they asked for".
#
# The workflow tag on the two payload-shaped events is derived from the edit
# shape rather than fixed (TDD §9: "W17 on apply-path events, W18 revision
# fields"), and it uses the same dominance rule as the Change row's own ``kind``
# column (``services/shaping.py`` ``_change_kind``): a payload that adds anything
# is W17 even when it also revises, because growth is the part the learner came
# for. One rule, two spellings would be one too many.


def _shape_workflow(*, n_add_lessons: int) -> str:
    """W17 for a payload that adds, W18 for a revision-only one.

    Agrees with ``_change_kind``'s dominance rule by the
    ``AddLessonsOperation.lessons`` ``min_length=1`` invariant: any
    Addition operation contributes at least one lesson, so
    "lesson count > 0" and "an Addition is present" are the same test.
    """
    return _W_SHAPE_ADD if n_add_lessons else _W_SHAPE_REVISE


def emit_shaping_conversation_started(
    *, account_id: uuid.UUID, path_id: uuid.UUID
) -> None:
    """A path's **shaping** conversation row was created (W17, TDD §9).

    The 2A twin's rule exactly, on the second thread: one shaping conversation
    per path, created lazily on the first turn that settles (D2), so this fires
    once per path and only for a turn that actually persisted. It is emitted
    after the settle commit, off the upsert's own ``created`` flag, so it can
    never claim a thread a rolled-back transaction did not leave behind.
    """
    _emit_guarded(
        SHAPING_CONVERSATION_STARTED,
        account_id=str(account_id),
        path_id=str(path_id),
        workflow=_W_SHAPE_ADD,
    )


def emit_shaping_message_sent(
    *, account_id: uuid.UUID, path_id: uuid.UUID, source: str
) -> None:
    """A shaping turn was **admitted** — every pre-stream gate passed (W17).

    Admission, not persistence, for 2A's reason: a reply that then fails is our
    failure, not an un-asked question, and D2 persists nothing on failure — so
    waiting for a settled turn would silently drop from shaping adoption exactly
    the learners the surface failed. A send refused before admission (409 not
    ready, 409 in-flight, 429 cap) emits nothing: it never became a turn.

    ``source`` is ``typed`` or ``suggestion`` (:class:`~aleph.models.MessageSource`)
    — the shaping rail's four §5.3 suggestions send as ``suggestion``.
    """
    _emit_guarded(
        SHAPING_MESSAGE_SENT,
        account_id=str(account_id),
        path_id=str(path_id),
        source=source,
        workflow=_W_SHAPE_ADD,
    )


def emit_shaping_reply_completed(
    *,
    account_id: uuid.UUID,
    path_id: uuid.UUID,
    outcome: str,
    duration_ms: int,
    has_proposal: bool,
    ttft_ms: int | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> None:
    """A shaping reply resolved, **however it resolved** (TDD §9).

    ``outcome`` is ``success`` / ``failure`` / ``stopped``, and every rule 2A's
    twin documents holds unchanged on this rail: ``stopped`` is the learner
    ending their own turn and is not a fault; ``ttft_ms`` is ``None`` (never
    ``0``) when no delta ever reached the wire; ``duration_ms`` is clocked from
    before the shared concurrency permit, so it includes queue wait and can
    exceed ``TUTOR_REPLY_TIMEOUT``.

    **A declined edit is a ``success``** (W20). An out-of-vocabulary ask —
    remove a unit, reorder lessons, revise a lesson the learner has finished —
    is answered gracefully as a real, persisted turn, and is deliberately not
    machine-tagged this phase (D5, PRD §5.7b). It reads here as a success with
    ``has_proposal=False``, which is the only trace of it in the events; the
    distinction lives in the reply text and in the evals.

    ``has_proposal`` therefore does two jobs: it is the denominator half of
    proposal acceptance (of the replies that offered an edit, how many were
    applied), and it is what makes a decline distinguishable from a
    conversational turn that was never about an edit at all — which it is not,
    and that limit is documented in docs/metrics.md rather than papered over.

    Unlike 2A's, this event is **not** the only shaping latency signal that
    matters, but it is the only one PRD §7's "2A's budgets apply to this surface
    unchanged" guardrail can be computed from.
    """
    _emit_guarded(
        SHAPING_REPLY_COMPLETED,
        account_id=str(account_id),
        path_id=str(path_id),
        outcome=outcome,
        success=outcome == "success",
        ttft_ms=ttft_ms,
        duration_ms=duration_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        has_proposal=has_proposal,
        workflow=_W_SHAPE_ADD,
    )


def emit_proposal_shown(
    *,
    account_id: uuid.UUID,
    path_id: uuid.UUID,
    n_add_lessons: int,
    n_revisions: int,
    new_unit: bool,
) -> None:
    """A **Proposal** card reached the rail (W17/W18, TDD §9).

    Emitted where the validated payload becomes the ``proposal`` SSE frame,
    mid-stream, which is what "shown" means — 2A's ``tutor_check_shown`` rule
    verbatim, including its consequence: a card shown on a reply that then fails
    still counts as shown, though D2 persisted nothing and it can never be
    applied. That inflates the denominator of proposal acceptance slightly, and
    docs/metrics.md says so.

    The three counts are the §7 edit-shape mix, read straight off the payload:

    * ``n_add_lessons`` counts **lessons**, not operations — an Addition of
      three lessons is three, because the mix question is how much path the
      learner asked for, and one operation carrying three lessons is not the
      same ask as one carrying one.
    * ``n_revisions`` counts **operations**, because a Revision is exactly one
      lesson by construction (D1) — so the two counts are commensurable.
    * ``new_unit`` is whether any Addition brought a new unit with it: the
      "this is a new topic, not more of the same" signal.
    """
    _emit_guarded(
        PROPOSAL_SHOWN,
        account_id=str(account_id),
        path_id=str(path_id),
        n_add_lessons=n_add_lessons,
        n_revisions=n_revisions,
        new_unit=new_unit,
        workflow=_shape_workflow(n_add_lessons=n_add_lessons),
    )


def emit_change_applied(
    *,
    account_id: uuid.UUID,
    path_id: uuid.UUID,
    change_id: uuid.UUID,
    n_add_lessons: int,
    n_revisions: int,
    new_unit: bool,
    lesson_ids: Sequence[str],
) -> None:
    """The learner tapped **Apply** and the Change committed (W17/W18, TDD §9).

    Emitted after the commit, so the event never claims structure a rolled-back
    transaction did not leave behind. The counts are :func:`emit_proposal_shown`'s,
    re-derived from the *stored* payload rather than carried from the card: what
    landed is what the apply re-validated against live state (D5), which is not
    necessarily what was proposed minutes earlier.

    ``lesson_ids`` is this phase's answer to "shaping is path-level, so there is
    no ``lesson_id``": the ids of every lesson this Change **created or
    revised**, which is precisely the set the primary metric (shaping yield)
    joins later ``quick_check_attempted`` / ``lesson_completed`` records against
    to ask whether the learner actually engaged with what they asked for. Created
    ids come from the Change's own inverse (the rows it inserted); revised ids
    from its operations. Logfire carries a list attribute as JSON text, so the
    saved queries read it as ``(attributes ->> 'lesson_ids')::jsonb`` and unnest
    — see docs/metrics.md.
    """
    _emit_guarded(
        CHANGE_APPLIED,
        account_id=str(account_id),
        path_id=str(path_id),
        change_id=str(change_id),
        n_add_lessons=n_add_lessons,
        n_revisions=n_revisions,
        new_unit=new_unit,
        lesson_ids=list(lesson_ids),
        workflow=_shape_workflow(n_add_lessons=n_add_lessons),
    )


def emit_change_undone(
    *,
    account_id: uuid.UUID,
    path_id: uuid.UUID,
    change_id: uuid.UUID,
    minutes_since_apply: float,
) -> None:
    """A Change was **undone** and the path restored exactly (W19, TDD §9).

    Emitted after the undo commit, for :func:`emit_change_applied`'s reason. The
    §7 undo rate is this count over ``change_applied``'s, and it is a guardrail
    on *proposal quality*: regret is the signal that consent did not work.

    ``minutes_since_apply`` is the time-to-undo half of that metric, measured
    from the Change row's own ``applied_at``. It is **fractional minutes**, not
    whole ones: the most interesting undo is the immediate "oh, not that" a few
    seconds after the tap, and rounding would file every one of those as zero
    and flatten the distribution the metric exists to read.
    """
    _emit_guarded(
        CHANGE_UNDONE,
        account_id=str(account_id),
        path_id=str(path_id),
        change_id=str(change_id),
        minutes_since_apply=minutes_since_apply,
        workflow=_W_SHAPE_UNDO,
    )


# --- flashcards & spaced repetition (Phase 3, TDD §9) -------------------------- #
#
# Three events, no session events (the module docstring above / TDD §9's "do
# not add an event you can compute" argument): ``review_graded`` carries
# ``queue_size``/``queue_remaining`` precisely so a session start (first grade
# of a day) and a session finish (``queue_remaining == 0``) are both derivable
# from it alone.

_FLASHCARDS_DRAFTED_WORKFLOW = {
    "generated": _W_DUE_CARD,
    "failed": _W_FAILURE,
}


def emit_flashcards_drafted(
    *,
    account_id: uuid.UUID,
    path_id: uuid.UUID | None,
    lesson_id: uuid.UUID,
    position_in_path: int,
    drafted_count: int,
    outcome: str,
    duration_ms: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> None:
    """A drafting run resolved, **however it resolved** (W24/W8, TDD §9).

    ``outcome`` is ``generated`` (W24) or ``failed`` (W8, the same generic
    generation-failure tag ``lesson_generated``/``outline_generated`` use for
    their own failed branch) — the flashcard agent has no refusal branch
    (TDD §5.2: the lesson it drafts from was already generated), so there is
    no third outcome to carry. ``success`` is the boolean form for the
    keep-rate/failure-rate reading; ``drafted_count`` is ``0`` on a failed
    run (nothing was persisted) and the agent's actual card count on a
    ``generated`` one. The token triple is the drafting agent's own usage,
    zero when the run never completed — the same reading
    ``lesson_generated``'s triple gives Phase 1.

    Emitted only on a **fenced win** — the same
    ``services/generation.py::_run_claimed_lesson`` precedent: a lesson whose
    content vanished or was never generated before drafting was claimed emits
    **no** event at all, because there is no ``account_id``/``path_id`` to
    stamp it with and the cause is referential breakage, not a
    drafting-quality failure.

    That is the *documented* gap. There is a second, undocumented one: a
    **crashed worker** (a Fly machine restart, a task cancelled at shutdown —
    neither caught by this module's own top-level ``except Exception``)
    leaves its ``flashcard_draft_runs`` row ``generating`` forever. That row
    is only ever collapsed to ``failed`` on *read*
    (:meth:`~aleph.repositories.flashcards.FlashcardRepository.
    get_effective_draft_run_state`), never resolved, so no event is ever
    emitted for it — this function simply never runs again for that
    ``lesson_id``. A drafting failure rate computed from this event
    undercounts exactly the failure mode that matters most; see
    ``docs/metrics.md``.

    ``path_id`` is nullable for the same reason ``review_graded``'s is: the
    source path can be deleted out from under a run already in flight
    (drafting spans a model call), and D12's degrade is honest here too
    rather than papered over with a stale id.
    """
    _emit(
        FLASHCARDS_DRAFTED,
        account_id=str(account_id),
        path_id=str(path_id) if path_id is not None else None,
        lesson_id=str(lesson_id),
        position_in_path=position_in_path,
        drafted_count=drafted_count,
        outcome=outcome,
        success=outcome == "generated",
        duration_ms=duration_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        workflow=_FLASHCARDS_DRAFTED_WORKFLOW.get(outcome, _W_DUE_CARD),
    )


def emit_flashcards_kept(
    *,
    account_id: uuid.UUID,
    path_id: uuid.UUID | None,
    lesson_id: uuid.UUID,
    drafted_count: int,
    kept_count: int,
) -> None:
    """A learner resolved the keep screen for one lesson's drafts (W24, TDD §9).

    Both counts ride **one** record by design (TDD §9): keep rate is a ratio
    computed inside a single row rather than a join between two event
    streams, which is what makes it immune to ``flashcards_drafted``'s own
    resolution timing (a run can fail and be retried before a learner ever
    sees a keep screen). ``kept_ids: []`` — "Skip — keep none" (D6) — is the
    same event with ``kept_count=0``, not a distinct event; a request against
    a lesson with no pending drafts at all never reaches this emitter (the
    caller only calls it when at least one draft existed to act on).

    ``path_id`` is nullable for the same D12 reason as ``review_graded``'s: a
    draft's source path can be deleted between drafting and the keep request.

    Routed through :func:`_emit_guarded`: this fires inside
    ``keep_flashcard_drafts``'s open write transaction, ahead of the router's
    commit, so a raising sink must not turn an already-valid keep into a
    ``500`` and lose it (see :func:`_emit_guarded`'s own docstring).
    """
    _emit_guarded(
        FLASHCARDS_KEPT,
        account_id=str(account_id),
        path_id=str(path_id) if path_id is not None else None,
        lesson_id=str(lesson_id),
        drafted_count=drafted_count,
        kept_count=kept_count,
        workflow=_W_DUE_CARD,
    )


_REVIEW_GRADED_WORKFLOW = {
    "again": _W_LAPSE_RETURNS,
    "got_it": _W_QUEUE_HOLDS,
}


def emit_review_graded(
    *,
    account_id: uuid.UUID,
    card_id: uuid.UUID,
    path_id: uuid.UUID | None,
    grade: str,
    rung_before: int,
    queue_size: int,
    queue_remaining: int,
) -> None:
    """Every grade (W25/W26, TDD §9) — the one event this phase's whole review
    surface is read from, session events included (see the module note above).

    ``path_id`` is **nullable**, unlike every other event's locator: an
    orphaned card (D12 — its source path was deleted) still reviews, with a
    degraded citation, and grading it is exactly W27's "a card survives its
    source lesson" in action. ``None`` here, never a placeholder id, is what
    keeps that case honestly distinct rather than silently mis-attributed to
    a path the card no longer has.

    ``grade`` is the wire value (``again`` / ``got_it``) and doubles as the
    workflow selector: ``again`` is W26 (a lapse resurfacing), ``got_it`` is
    W25 (the ordinary queue-draining case) — the same payload-derived
    dominance rule ``_shape_workflow`` uses, spelled as a dict rather than a
    function since there is no ``count > 0`` style combination to resolve.
    ``rung_before`` is the client-validated optimistic-concurrency token, not
    re-read from the row, so it is exactly what the request asked to move
    from. ``queue_size``/``queue_remaining`` are today's selected-set size and
    the unsatisfied count *after* this grade resolves — the two integers that
    make both session-started and session-finished derivable with no session
    event of their own (TDD §9).

    Routed through :func:`_emit_guarded`: this fires inside ``grade_card``'s
    open write transaction, ahead of the router's commit, so a raising sink
    must not turn an already-valid grade into a ``500`` and lose it (see
    :func:`_emit_guarded`'s own docstring).
    """
    _emit_guarded(
        REVIEW_GRADED,
        account_id=str(account_id),
        card_id=str(card_id),
        path_id=str(path_id) if path_id is not None else None,
        grade=grade,
        rung_before=rung_before,
        queue_size=queue_size,
        queue_remaining=queue_remaining,
        workflow=_REVIEW_GRADED_WORKFLOW.get(grade, _W_QUEUE_HOLDS),
    )


# --- the analyst (Phase 6, TDD §9/§15) ----------------------------------------- #
#
# Three events. ``beat_deployed`` is the deployment-mix datum and Beat
# survival's denominator; ``brief_research_completed`` is one
# research-run-resolution event carrying the §15 funnel, fired however the
# run resolved (never a success event plus a separate failure event — the
# ``lesson_generated``/``tutor_reply_completed`` shape, copied here so a rate
# can be computed from one source); ``brief_read`` is the north star's own
# read ping. AL-070's contract applies unchanged: every field a §9 query
# reads is one this module actually emits.


def emit_beat_deployed(
    *,
    account_id: uuid.UUID,
    beat_id: uuid.UUID,
    beat_level: str,
    anchor_weekday: int,
    has_guidance: bool,
) -> None:
    """A learner deployed a Beat (W29) — Beat survival's own denominator and
    the deployment-mix datum (TDD §9).

    Fires from ``routers/v1/beats.py::deploy_beat`` after the Beat row is
    created and committed and the arrival drain has claimed and spawned its
    first research run — the ``path_created`` precedent (``services/
    generation.py::create_path``), one workload over: the analogous "the row
    exists, committed" moment for a Beat's own creation. The Level rides as
    ``beat_level``, **not** ``level`` — ``path_created``'s ``path_level``
    reason applies verbatim: structlog's ``add_log_level`` processor owns the
    bare ``level`` key (the log severity) and silently clobbers it.
    ``has_guidance`` is whether the Beat carries an optional Guidance string
    — the same standing-orders shape a path's does, deployment-mix's finer
    axis.

    Routed through :func:`_emit_guarded`: it runs in the request path, after
    the Beat already exists and the drain has already spawned its first run
    — a raising sink must not turn an already-deployed Beat into a ``500``
    the learner would read as "nothing happened" (the ``change_applied``
    argument, one surface over).
    """
    _emit_guarded(
        BEAT_DEPLOYED,
        account_id=str(account_id),
        beat_id=str(beat_id),
        beat_level=beat_level,
        anchor_weekday=anchor_weekday,
        has_guidance=has_guidance,
        workflow=_W_BEAT_DEPLOYED,
    )


_BRIEF_RESEARCH_WORKFLOW = {
    "published": _W_BEAT_DEPLOYED,
    "skipped": _W_BRIEF_SKIPPED,
    "failed": _W_FAILURE,
    "refused": _W_REFUSAL,
}


def emit_brief_research_completed(
    *,
    account_id: uuid.UUID,
    beat_id: uuid.UUID,
    outcome: str,
    duration_ms: int,
    queries: int,
    documents_retrieved: int,
    documents_after_filters: int,
    findings: int,
    survivors: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> None:
    """A research run resolved, **however it resolved** (TDD §9/§15) — one
    event with an ``outcome``, never a success event and a separate failure
    event, the same shape ``lesson_generated``/``tutor_reply_completed``
    already use, copied here so a rate needs only one source.

    ``outcome`` is one of ``published`` (a Brief with Sources was
    persisted), ``skipped`` (the novelty gate found no survivors —
    "the feature working correctly", CONTEXT.md: Skipped), ``failed`` (any
    of TDD §5.7's infra/provider/invariant branches — retrieval unavailable,
    zero documents surviving §5.2's filters, a model timeout, an exhausted
    writer retry budget, a construction-time invariant violation, or the
    persist-boundary "no Sources" guard — folded into one wire value because
    every one of them is equally retryable and equally "not a Brief"), or
    ``refused`` (the researcher's safety branch, terminal — TDD D3).

    **The funnel is the point (TDD §15), not any single count.**
    ``documents_retrieved`` is the raw, pre-filter count the ``Retriever``
    itself returned (``services/retrieval.py::retrieve``'s
    ``record_raw_count`` hook); ``documents_after_filters`` is what survived
    ``retrieve()``'s own dedupe/dated/non-empty/cap/budget filters (§5.2) —
    the same count the "zero documents after filters -> failed" branch
    checks. ``findings`` is the researcher's own raw count; ``survivors`` is
    what the novelty gate actually let through to a Brief. Together the four
    separate a genuinely quiet week (healthy retrieval, healthy findings,
    zero survivors — the gate working) from thin retrieval RECALL (low
    documents, low findings, zero survivors — we found little to read) from
    thin retrieval PRECISION (healthy documents, low findings, zero
    survivors — we read plenty and it was chum): raw Skip rate alone cannot
    tell those three apart, and this is the instrument that does (see
    ``queries/logfire/brief_skip_rate.sql``). Every count is the
    **best-known value at the point the run stopped** — a run that fails
    before the researcher is ever called reports ``findings=0``,
    ``survivors=0`` honestly, never a placeholder standing in for "unknown".

    The token triple is ``usage_tokens``' (``services/generation.py``)
    reading of **both** model calls one run can make — researcher and
    analyst — summed, because D14a's $0.50 ceiling is arithmetic over the
    whole run's cost, not either call alone; these fields are what makes
    that ceiling **verified rather than asserted** (TDD §9: "the design
    target is arithmetic, the event is the measurement, and if they
    disagree the event wins"). Zero on any branch that never reached (or
    never completed) the corresponding call.

    ``duration_ms`` measures the **run**, never the request: clocked from
    the moment the claimed pipeline starts (``BriefingService._run_claimed``,
    before its own context load) through retrieval, both model calls, and
    persist. This fires from a background task
    (``BriefingService.run_research``, spawned by the arrival drain or the
    explicit retry trigger) that the request which spawned it never awaits,
    so no HTTP round trip — nor any queueing before the spawn — is ever
    inside this number.

    Emitted only on a **fenced-win** mark (the ``outline_generated``/
    ``lesson_generated`` precedent): a lost race's mark is a silent no-op
    (``BriefingService``'s own ``_mark_failed``/``_mark_refused``/
    ``_persist_published``/``_persist_skipped`` all return whether *this*
    call still owned the claim), so one run resolves this metric exactly
    once, from whichever caller actually won the fence.
    """
    _emit(
        BRIEF_RESEARCH_COMPLETED,
        account_id=str(account_id),
        beat_id=str(beat_id),
        outcome=outcome,
        duration_ms=duration_ms,
        queries=queries,
        documents_retrieved=documents_retrieved,
        documents_after_filters=documents_after_filters,
        findings=findings,
        survivors=survivors,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        workflow=_BRIEF_RESEARCH_WORKFLOW.get(outcome, _W_BEAT_DEPLOYED),
    )


def emit_brief_read(
    *,
    account_id: uuid.UUID,
    beat_id: uuid.UUID,
    brief_id: uuid.UUID,
    marker: str,
    age_days: int,
) -> None:
    """A learner opened a Brief, or reached its Sources (W29) — the north
    star's own event, and ``brief_read_rate``/``brief_depth_of_read``'s
    source (TDD §9).

    **Never in the activation query, on purpose** (TDD §15's standing rule,
    PRD §4.9): reading a Brief does not touch **Activated learner** —
    ``queries/logfire/activation_rate.sql`` reads only ``account_created``,
    ``lesson_completed``, ``quick_check_attempted``, and this event is not
    one of them. Nothing in this module enforces that by construction (it
    cannot — the two are unrelated files); it holds because adding
    ``brief_read`` there would be a deliberate act, and
    ``tests/unit/test_metrics_queries.py`` pins the query's text to keep it
    that way.

    ``marker`` is ``opened`` or ``sources`` (:class:`~aleph.dtos.beats.
    ReadPingMarker`) — Depth of read is ``sources`` ÷ ``opened``.
    ``age_days`` is how many days old the Brief was when this ping landed
    (its ``published_on`` to the ping's own day) — the "still fresh, or a
    backlog" datum a raw open count cannot carry alone.

    Fires from ``routers/v1/beats.py::read_brief`` only on the real,
    first-write-wins transition (``BriefRepository.mark_read``/
    ``mark_sources_seen`` returning ``True``) — a repeat ping for a marker
    already stamped is not a second read, the ``quick_check_attempted``
    precedent (CONTEXT.md/AL-012: a repeat submit is not a new Attempt).
    Routed through :func:`_emit_guarded`: it fires after the ping's own
    commit, so a raising sink must not turn an already-recorded read into a
    ``500`` (the ``change_applied`` argument, one surface over).
    """
    _emit_guarded(
        BRIEF_READ,
        account_id=str(account_id),
        beat_id=str(beat_id),
        brief_id=str(brief_id),
        marker=marker,
        age_days=age_days,
        workflow=_W_BEAT_DEPLOYED,
    )
