"""Unit tests for the product-event emission seam (AL-070, PRD §5.7, TDD §9).

Product events are structured Logfire events emitted from services/routers so the
§7 success metrics are computable as saved Logfire SQL over the emitted records.
These tests pin the *exact* field set every event carries (its ``account_id`` /
``path_id`` / ``lesson_id`` / ``position_in_path`` / ``workflow`` and outcome
fields) and anchor the ``EVENT_FIELDS`` manifest to real emission — the manifest
is what ``test_metrics_queries`` checks each SQL query against, so it must never
drift from what the emitters actually log.

Capture is at the structlog seam (a stub logger recording the exact kwargs passed
to ``.info``) rather than through Logfire: ``test_logging`` already proves the
StructlogProcessor carries these fields into Logfire, and a stub gives the precise
field set without Logfire's own record attributes as noise (fakes over mocks —
the assertion here *is* the emitted interaction, TDD §12).
"""

from __future__ import annotations

import uuid

import pytest

from aleph import events


class _Recorder:
    """Records the exact ``(event, fields)`` passed to a captured logger."""

    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, object]]] = []

    def get_logger(self, _name: str) -> object:
        records = self.records

        class _Logger:
            def info(self, event: str, **fields: object) -> None:
                records.append((event, fields))

        return _Logger()


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(events.structlog, "get_logger", rec.get_logger)
    return rec


ACCOUNT = uuid.UUID("11111111-1111-4111-8111-111111111111")
PATH = uuid.UUID("22222222-2222-4222-8222-222222222222")
LESSON = uuid.UUID("33333333-3333-4333-8333-333333333333")
BEAT = uuid.UUID("77777777-7777-4777-8777-777777777777")
BRIEF = uuid.UUID("88888888-8888-4888-8888-888888888888")


def test_account_created(recorder: _Recorder) -> None:
    events.emit_account_created(account_id=ACCOUNT)
    assert recorder.records == [
        ("account_created", {"account_id": str(ACCOUNT), "workflow": "W1"})
    ]


def test_path_created(recorder: _Recorder) -> None:
    events.emit_path_created(account_id=ACCOUNT, path_id=PATH, path_level="new_to_it")
    assert recorder.records == [
        (
            "path_created",
            {
                "account_id": str(ACCOUNT),
                "path_id": str(PATH),
                # ``path_level`` (not ``level``): structlog reserves ``level``.
                "path_level": "new_to_it",
                "workflow": "W1",
            },
        )
    ]


@pytest.mark.parametrize(
    ("outcome", "success", "workflow"),
    [("ready", True, "W1"), ("failed", False, "W8"), ("refused", False, "W7")],
)
def test_outline_generated_outcomes(
    recorder: _Recorder, outcome: str, success: bool, workflow: str
) -> None:
    events.emit_outline_generated(
        account_id=ACCOUNT,
        path_id=PATH,
        outcome=outcome,
        duration_ms=1234,
        prompt_tokens=408,
        completion_tokens=105,
        total_tokens=513,
    )
    name, fields = recorder.records[0]
    assert name == "outline_generated"
    assert fields == {
        "account_id": str(ACCOUNT),
        "path_id": str(PATH),
        "outcome": outcome,
        "success": success,
        "duration_ms": 1234,
        "prompt_tokens": 408,
        "completion_tokens": 105,
        "total_tokens": 513,
        "workflow": workflow,
    }


@pytest.mark.parametrize(
    ("outcome", "success", "workflow"),
    [("generated", True, "W1"), ("failed", False, "W8")],
)
def test_lesson_generated_outcomes(
    recorder: _Recorder, outcome: str, success: bool, workflow: str
) -> None:
    events.emit_lesson_generated(
        account_id=ACCOUNT,
        path_id=PATH,
        lesson_id=LESSON,
        position_in_path=3,
        outcome=outcome,
        duration_ms=999,
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
    )
    name, fields = recorder.records[0]
    assert name == "lesson_generated"
    assert fields == {
        "account_id": str(ACCOUNT),
        "path_id": str(PATH),
        "lesson_id": str(LESSON),
        "position_in_path": 3,
        "outcome": outcome,
        "success": success,
        "duration_ms": 999,
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "total_tokens": 30,
        "workflow": workflow,
    }


def test_lesson_viewed(recorder: _Recorder) -> None:
    events.emit_lesson_viewed(
        account_id=ACCOUNT, path_id=PATH, lesson_id=LESSON, position_in_path=1
    )
    assert recorder.records == [
        (
            "lesson_viewed",
            {
                "account_id": str(ACCOUNT),
                "path_id": str(PATH),
                "lesson_id": str(LESSON),
                "position_in_path": 1,
                "workflow": "W1",
            },
        )
    ]


@pytest.mark.parametrize(
    ("outcome", "is_correct"), [("correct", True), ("incorrect", False)]
)
def test_quick_check_attempted(
    recorder: _Recorder, outcome: str, is_correct: bool
) -> None:
    events.emit_quick_check_attempted(
        account_id=ACCOUNT,
        path_id=PATH,
        lesson_id=LESSON,
        position_in_path=2,
        outcome=outcome,
    )
    name, fields = recorder.records[0]
    assert name == "quick_check_attempted"
    assert fields == {
        "account_id": str(ACCOUNT),
        "path_id": str(PATH),
        "lesson_id": str(LESSON),
        "position_in_path": 2,
        "outcome": outcome,
        "is_correct": is_correct,
        "workflow": "W6",
    }


def test_lesson_completed(recorder: _Recorder) -> None:
    events.emit_lesson_completed(
        account_id=ACCOUNT, path_id=PATH, lesson_id=LESSON, position_in_path=4
    )
    assert recorder.records == [
        (
            "lesson_completed",
            {
                "account_id": str(ACCOUNT),
                "path_id": str(PATH),
                "lesson_id": str(LESSON),
                "position_in_path": 4,
                "workflow": "W1",
            },
        )
    ]


def test_path_completed(recorder: _Recorder) -> None:
    events.emit_path_completed(account_id=ACCOUNT, path_id=PATH, lesson_count=6)
    assert recorder.records == [
        (
            "path_completed",
            {
                "account_id": str(ACCOUNT),
                "path_id": str(PATH),
                "lesson_count": 6,
                "workflow": "W3",
            },
        )
    ]


def test_path_deleted(recorder: _Recorder) -> None:
    events.emit_path_deleted(account_id=ACCOUNT, path_id=PATH)
    assert recorder.records == [
        (
            "path_deleted",
            {"account_id": str(ACCOUNT), "path_id": str(PATH), "workflow": "W5"},
        )
    ]


# --------------------------------------------------------------------------- #
# The tutor (Phase 2, AL-240 / TDD §9)
# --------------------------------------------------------------------------- #


def test_tutor_conversation_started(recorder: _Recorder) -> None:
    events.emit_tutor_conversation_started(
        account_id=ACCOUNT, path_id=PATH, lesson_id=LESSON, position_in_path=5
    )
    assert recorder.records == [
        (
            "tutor_conversation_started",
            {
                "account_id": str(ACCOUNT),
                "path_id": str(PATH),
                "lesson_id": str(LESSON),
                "position_in_path": 5,
                "workflow": "W9",
            },
        )
    ]


@pytest.mark.parametrize("source", ["typed", "suggestion"])
def test_tutor_message_sent(recorder: _Recorder, source: str) -> None:
    """``source`` is the §7 entry-mix datum: suggestion vs free text."""
    events.emit_tutor_message_sent(
        account_id=ACCOUNT,
        path_id=PATH,
        lesson_id=LESSON,
        position_in_path=5,
        source=source,
    )
    name, fields = recorder.records[0]
    assert name == "tutor_message_sent"
    assert fields == {
        "account_id": str(ACCOUNT),
        "path_id": str(PATH),
        "lesson_id": str(LESSON),
        "position_in_path": 5,
        "source": source,
        "workflow": "W9",
    }


@pytest.mark.parametrize(
    ("outcome", "success", "workflow"),
    [("success", True, "W9"), ("failure", False, "W14"), ("stopped", False, "W9")],
)
def test_tutor_reply_completed_outcomes(
    recorder: _Recorder, outcome: str, success: bool, workflow: str
) -> None:
    """Every resolution emits, and only ``failure`` is tagged W14.

    A ``stopped`` reply is the learner ending their own turn, not a fault, so it
    stays on W9 — tagging it W14 would put learner behaviour in the
    failure-rate workflow slice.
    """
    events.emit_tutor_reply_completed(
        account_id=ACCOUNT,
        path_id=PATH,
        lesson_id=LESSON,
        position_in_path=5,
        outcome=outcome,
        ttft_ms=210,
        duration_ms=1900,
        prompt_tokens=1200,
        completion_tokens=300,
        total_tokens=1500,
    )
    name, fields = recorder.records[0]
    assert name == "tutor_reply_completed"
    assert fields == {
        "account_id": str(ACCOUNT),
        "path_id": str(PATH),
        "lesson_id": str(LESSON),
        "position_in_path": 5,
        "outcome": outcome,
        "success": success,
        "ttft_ms": 210,
        "duration_ms": 1900,
        "prompt_tokens": 1200,
        "completion_tokens": 300,
        "total_tokens": 1500,
        "workflow": workflow,
    }


def test_tutor_reply_completed_carries_a_null_ttft_when_no_delta_arrived(
    recorder: _Recorder,
) -> None:
    """A reply that never produced a token has no time-to-first-token.

    ``None`` rather than 0: a zero would be indistinguishable from an instant
    first token and would drag the TTFT percentile panel down. The field is
    still always present (the manifest is a fixed set), and the percentile SQL
    skips NULLs.
    """
    events.emit_tutor_reply_completed(
        account_id=ACCOUNT,
        path_id=PATH,
        lesson_id=LESSON,
        position_in_path=5,
        outcome="failure",
        ttft_ms=None,
        duration_ms=30000,
    )
    _name, fields = recorder.records[0]
    assert fields["ttft_ms"] is None
    assert fields["duration_ms"] == 30000
    # Usage is optional at the call site but never absent from the record.
    assert fields["prompt_tokens"] == 0
    assert fields["total_tokens"] == 0


def test_tutor_check_shown(recorder: _Recorder) -> None:
    events.emit_tutor_check_shown(
        account_id=ACCOUNT, path_id=PATH, lesson_id=LESSON, position_in_path=5
    )
    assert recorder.records == [
        (
            "tutor_check_shown",
            {
                "account_id": str(ACCOUNT),
                "path_id": str(PATH),
                "lesson_id": str(LESSON),
                "position_in_path": 5,
                "workflow": "W12",
            },
        )
    ]


@pytest.mark.parametrize(
    ("outcome", "is_correct"), [("correct", True), ("incorrect", False)]
)
@pytest.mark.parametrize("first_answer", [True, False])
def test_tutor_check_answered(
    recorder: _Recorder, outcome: str, is_correct: bool, first_answer: bool
) -> None:
    """A re-answer emits too, distinguished by ``first_answer`` (AL-240 D).

    Unlike the Quick check's first-wins Attempt, answering a Tutor check again
    genuinely rewrites the stored payload, so the event fires on every real
    write; ``first_answer`` is what keeps a per-check rate honest.
    """
    events.emit_tutor_check_answered(
        account_id=ACCOUNT,
        path_id=PATH,
        lesson_id=LESSON,
        position_in_path=5,
        outcome=outcome,
        first_answer=first_answer,
    )
    name, fields = recorder.records[0]
    assert name == "tutor_check_answered"
    assert fields == {
        "account_id": str(ACCOUNT),
        "path_id": str(PATH),
        "lesson_id": str(LESSON),
        "position_in_path": 5,
        "outcome": outcome,
        "is_correct": is_correct,
        "first_answer": first_answer,
        "workflow": "W12",
    }


# --------------------------------------------------------------------------- #
# Shaping (Phase 2B, AL-340 / TDD §9)
#
# No ``lesson_id`` and no ``position_in_path`` anywhere below: shaping is
# path-level (PRD §5.1), and the operations' lesson ids ride payload-derived
# fields on ``change_applied`` instead.
# --------------------------------------------------------------------------- #

CHANGE = uuid.UUID("44444444-4444-4444-8444-444444444444")


def test_shaping_conversation_started(recorder: _Recorder) -> None:
    events.emit_shaping_conversation_started(account_id=ACCOUNT, path_id=PATH)
    assert recorder.records == [
        (
            "shaping_conversation_started",
            {"account_id": str(ACCOUNT), "path_id": str(PATH), "workflow": "W17"},
        )
    ]


@pytest.mark.parametrize("source", ["typed", "suggestion"])
def test_shaping_message_sent(recorder: _Recorder, source: str) -> None:
    """``source`` is 2A's entry-mix datum on the shaping rail's four suggestions."""
    events.emit_shaping_message_sent(account_id=ACCOUNT, path_id=PATH, source=source)
    name, fields = recorder.records[0]
    assert name == "shaping_message_sent"
    assert fields == {
        "account_id": str(ACCOUNT),
        "path_id": str(PATH),
        "source": source,
        "workflow": "W17",
    }


@pytest.mark.parametrize(
    ("outcome", "success"),
    [("success", True), ("failure", False), ("stopped", False)],
)
def test_shaping_reply_completed_outcomes(
    recorder: _Recorder, outcome: str, success: bool
) -> None:
    """Every resolution emits, and a declined edit is a ``success`` like a refusal.

    Phase 2B has no failure workflow of its own (2A's W14 has no 2B twin — TDD
    §9 puts W21 on the guardrail *queries*), so the tag stays W17 on all three
    and ``outcome``/``success`` are what the failure guardrail slices on.
    """
    events.emit_shaping_reply_completed(
        account_id=ACCOUNT,
        path_id=PATH,
        outcome=outcome,
        ttft_ms=180,
        duration_ms=2200,
        prompt_tokens=900,
        completion_tokens=250,
        total_tokens=1150,
        has_proposal=True,
    )
    name, fields = recorder.records[0]
    assert name == "shaping_reply_completed"
    assert fields == {
        "account_id": str(ACCOUNT),
        "path_id": str(PATH),
        "outcome": outcome,
        "success": success,
        "ttft_ms": 180,
        "duration_ms": 2200,
        "prompt_tokens": 900,
        "completion_tokens": 250,
        "total_tokens": 1150,
        "has_proposal": True,
        "workflow": "W17",
    }


def test_shaping_reply_completed_carries_a_null_ttft_when_no_delta_arrived(
    recorder: _Recorder,
) -> None:
    """2A's TTFT rule verbatim: ``None``, never ``0``, and usage defaults to 0."""
    events.emit_shaping_reply_completed(
        account_id=ACCOUNT,
        path_id=PATH,
        outcome="failure",
        ttft_ms=None,
        duration_ms=30000,
        has_proposal=False,
    )
    _name, fields = recorder.records[0]
    assert fields["ttft_ms"] is None
    assert fields["duration_ms"] == 30000
    assert fields["has_proposal"] is False
    assert fields["prompt_tokens"] == 0
    assert fields["total_tokens"] == 0


def test_proposal_shown_counts_the_edit_shapes(recorder: _Recorder) -> None:
    """``n_add_lessons`` counts **lessons**, ``n_revisions`` counts operations."""
    events.emit_proposal_shown(
        account_id=ACCOUNT,
        path_id=PATH,
        n_add_lessons=3,
        n_revisions=1,
        new_unit=True,
    )
    assert recorder.records == [
        (
            "proposal_shown",
            {
                "account_id": str(ACCOUNT),
                "path_id": str(PATH),
                "n_add_lessons": 3,
                "n_revisions": 1,
                "new_unit": True,
                # A payload that adds anything is W17 (the magic moment), even
                # when it also revises — the same dominance rule the Change
                # row's own ``kind`` column follows.
                "workflow": "W17",
            },
        )
    ]


def test_a_revision_only_proposal_is_tagged_w18(recorder: _Recorder) -> None:
    """TDD §9's "W18 revision fields": the shape decides the tag, not the event."""
    events.emit_proposal_shown(
        account_id=ACCOUNT, path_id=PATH, n_add_lessons=0, n_revisions=2, new_unit=False
    )
    _name, fields = recorder.records[0]
    assert fields["workflow"] == "W18"


def test_change_applied(recorder: _Recorder) -> None:
    """The primary metric's join key: the lesson ids the Change created/revised."""
    events.emit_change_applied(
        account_id=ACCOUNT,
        path_id=PATH,
        change_id=CHANGE,
        n_add_lessons=2,
        n_revisions=0,
        new_unit=False,
        lesson_ids=[str(LESSON)],
    )
    assert recorder.records == [
        (
            "change_applied",
            {
                "account_id": str(ACCOUNT),
                "path_id": str(PATH),
                "change_id": str(CHANGE),
                "n_add_lessons": 2,
                "n_revisions": 0,
                "new_unit": False,
                "lesson_ids": [str(LESSON)],
                "workflow": "W17",
            },
        )
    ]


def test_a_revision_only_change_is_tagged_w18(recorder: _Recorder) -> None:
    events.emit_change_applied(
        account_id=ACCOUNT,
        path_id=PATH,
        change_id=CHANGE,
        n_add_lessons=0,
        n_revisions=1,
        new_unit=False,
        lesson_ids=[str(LESSON)],
    )
    _name, fields = recorder.records[0]
    assert fields["workflow"] == "W18"


def test_change_undone(recorder: _Recorder) -> None:
    """``minutes_since_apply`` is the regret latency — fractional, never rounded
    to a whole minute, or every fast "oh no, undo" would read as zero."""
    events.emit_change_undone(
        account_id=ACCOUNT, path_id=PATH, change_id=CHANGE, minutes_since_apply=0.75
    )
    assert recorder.records == [
        (
            "change_undone",
            {
                "account_id": str(ACCOUNT),
                "path_id": str(PATH),
                "change_id": str(CHANGE),
                "minutes_since_apply": 0.75,
                "workflow": "W19",
            },
        )
    ]


CARD = uuid.UUID("66666666-6666-4666-8666-666666666666")


# --------------------------------------------------------------------------- #
# Flashcards & spaced repetition (Phase 3, AL-070 / TDD §9)
#
# No session events anywhere below: a session started is an account's first
# grade of a day and one finished is a grade with `queue_remaining = 0`, both
# derivable from `review_graded` alone (TDD §9).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("outcome", "success", "workflow"),
    [("generated", True, "W24"), ("failed", False, "W8")],
)
def test_flashcards_drafted_outcomes(
    recorder: _Recorder, outcome: str, success: bool, workflow: str
) -> None:
    """`failed` reuses W8, `lesson_generated`'s own generic failure tag —
    the flashcard agent has no refusal branch (TDD §5.2), so there is no
    third outcome to carry."""
    events.emit_flashcards_drafted(
        account_id=ACCOUNT,
        path_id=PATH,
        lesson_id=LESSON,
        position_in_path=2,
        drafted_count=4 if outcome == "generated" else 0,
        outcome=outcome,
        duration_ms=850,
        prompt_tokens=300,
        completion_tokens=120,
        total_tokens=420,
    )
    name, fields = recorder.records[0]
    assert name == "flashcards_drafted"
    assert fields == {
        "account_id": str(ACCOUNT),
        "path_id": str(PATH),
        "lesson_id": str(LESSON),
        "position_in_path": 2,
        "drafted_count": 4 if outcome == "generated" else 0,
        "outcome": outcome,
        "success": success,
        "duration_ms": 850,
        "prompt_tokens": 300,
        "completion_tokens": 120,
        "total_tokens": 420,
        "workflow": workflow,
    }


def test_flashcards_drafted_carries_no_path_id_for_an_orphaned_draft(
    recorder: _Recorder,
) -> None:
    """`path_id` is nullable (D12): a source path can be deleted mid-run."""
    events.emit_flashcards_drafted(
        account_id=ACCOUNT,
        path_id=None,
        lesson_id=LESSON,
        position_in_path=1,
        drafted_count=0,
        outcome="failed",
        duration_ms=10,
    )
    _name, fields = recorder.records[0]
    assert fields["path_id"] is None


def test_flashcards_kept(recorder: _Recorder) -> None:
    """Both counts ride one record (TDD §9): the keep-rate ratio lives inside
    a row rather than a join between two event streams."""
    events.emit_flashcards_kept(
        account_id=ACCOUNT,
        path_id=PATH,
        lesson_id=LESSON,
        drafted_count=4,
        kept_count=3,
    )
    assert recorder.records == [
        (
            "flashcards_kept",
            {
                "account_id": str(ACCOUNT),
                "path_id": str(PATH),
                "lesson_id": str(LESSON),
                "drafted_count": 4,
                "kept_count": 3,
                "workflow": "W24",
            },
        )
    ]


@pytest.mark.parametrize(("grade", "workflow"), [("got_it", "W25"), ("again", "W26")])
def test_review_graded_workflow_follows_the_grade(
    recorder: _Recorder, grade: str, workflow: str
) -> None:
    """`grade` doubles as the workflow selector: `again` is a lapse
    resurfacing (W26), `got_it` is the ordinary queue-draining case (W25)."""
    events.emit_review_graded(
        account_id=ACCOUNT,
        card_id=CARD,
        path_id=PATH,
        grade=grade,
        rung_before=2,
        queue_size=10,
        queue_remaining=6,
    )
    name, fields = recorder.records[0]
    assert name == "review_graded"
    assert fields == {
        "account_id": str(ACCOUNT),
        "card_id": str(CARD),
        "path_id": str(PATH),
        "grade": grade,
        "rung_before": 2,
        "queue_size": 10,
        "queue_remaining": 6,
        "workflow": workflow,
    }


def test_review_graded_carries_no_path_id_for_an_orphaned_card(
    recorder: _Recorder,
) -> None:
    """`path_id` is nullable: an orphaned card (its source path deleted, D12)
    still reviews, with `None` rather than a stale or placeholder id — W27's
    "a card survives its source lesson" in action."""
    events.emit_review_graded(
        account_id=ACCOUNT,
        card_id=CARD,
        path_id=None,
        grade="got_it",
        rung_before=1,
        queue_size=10,
        queue_remaining=9,
    )
    _name, fields = recorder.records[0]
    assert fields["path_id"] is None


# --------------------------------------------------------------------------- #
# The analyst (Phase 6, AL-540 / TDD §9/§15)
# --------------------------------------------------------------------------- #


def test_beat_deployed(recorder: _Recorder) -> None:
    events.emit_beat_deployed(
        account_id=ACCOUNT,
        beat_id=BEAT,
        beat_level="some_experience",
        anchor_weekday=0,
        has_guidance=True,
    )
    assert recorder.records == [
        (
            "beat_deployed",
            {
                "account_id": str(ACCOUNT),
                "beat_id": str(BEAT),
                "beat_level": "some_experience",
                "anchor_weekday": 0,
                "has_guidance": True,
                "workflow": "W29",
            },
        )
    ]


@pytest.mark.parametrize(
    ("outcome", "workflow"),
    [
        ("published", "W29"),
        ("skipped", "W31"),
        ("failed", "W8"),
        ("refused", "W7"),
    ],
)
def test_brief_research_completed_outcomes(
    recorder: _Recorder, outcome: str, workflow: str
) -> None:
    """All four outcomes (TDD §9/§15) — one event, never a success event plus
    a separate failure event, the shape ``lesson_generated``/
    ``tutor_reply_completed`` already use."""
    events.emit_brief_research_completed(
        account_id=ACCOUNT,
        beat_id=BEAT,
        outcome=outcome,
        duration_ms=42_000,
        queries=6,
        documents_retrieved=10,
        documents_after_filters=8,
        findings=3,
        survivors=1,
        prompt_tokens=900,
        completion_tokens=300,
        total_tokens=1200,
    )
    name, fields = recorder.records[0]
    assert name == "brief_research_completed"
    assert fields == {
        "account_id": str(ACCOUNT),
        "beat_id": str(BEAT),
        "outcome": outcome,
        "duration_ms": 42_000,
        "queries": 6,
        "documents_retrieved": 10,
        "documents_after_filters": 8,
        "findings": 3,
        "survivors": 1,
        "prompt_tokens": 900,
        "completion_tokens": 300,
        "total_tokens": 1200,
        "workflow": workflow,
    }


def test_brief_research_completed_funnel_is_honest_when_never_reached(
    recorder: _Recorder,
) -> None:
    """A run failed by the zero-documents-after-filters branch never called
    the researcher — ``findings``/``survivors`` are honestly ``0``, never a
    placeholder standing in for "unknown" (TDD §15)."""
    events.emit_brief_research_completed(
        account_id=ACCOUNT,
        beat_id=BEAT,
        outcome="failed",
        duration_ms=500,
        queries=6,
        documents_retrieved=0,
        documents_after_filters=0,
        findings=0,
        survivors=0,
    )
    _name, fields = recorder.records[0]
    assert fields["findings"] == 0
    assert fields["survivors"] == 0
    assert fields["prompt_tokens"] == 0
    assert fields["completion_tokens"] == 0
    assert fields["total_tokens"] == 0


@pytest.mark.parametrize("marker", ["opened", "sources"])
def test_brief_read(recorder: _Recorder, marker: str) -> None:
    events.emit_brief_read(
        account_id=ACCOUNT, beat_id=BEAT, brief_id=BRIEF, marker=marker, age_days=2
    )
    assert recorder.records == [
        (
            "brief_read",
            {
                "account_id": str(ACCOUNT),
                "beat_id": str(BEAT),
                "brief_id": str(BRIEF),
                "marker": marker,
                "age_days": 2,
                "workflow": "W29",
            },
        )
    ]


def test_a_failing_analyst_request_path_emitter_never_breaks_the_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``beat_deployed`` fires after the Beat already exists and the arrival
    drain has already spawned its first run; ``brief_read`` fires after its
    own ping commit. Neither may turn an already-real write into a 500 (the
    ``change_applied`` argument, one surface over) — both are routed through
    ``_emit_guarded``."""

    def _explode(_name: str) -> object:
        raise RuntimeError("logfire sink is down")

    monkeypatch.setattr(events.structlog, "get_logger", _explode)

    events.emit_beat_deployed(
        account_id=ACCOUNT,
        beat_id=BEAT,
        beat_level="some_experience",
        anchor_weekday=0,
        has_guidance=False,
    )
    events.emit_brief_read(
        account_id=ACCOUNT, beat_id=BEAT, brief_id=BRIEF, marker="opened", age_days=0
    )


def test_a_failing_shaping_emitter_never_breaks_the_request_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telemetry cannot fail an apply that has already committed (TDD §9).

    The shaping stamps sit *after* their commit (``change_applied``,
    ``change_undone``, ``shaping_conversation_started``) or beside a held
    reservation (``shaping_message_sent``), so a raising sink would turn a
    landed change into a 500 or wedge a conversation. See
    ``events._emit_guarded`` for the full list of emitters this guard covers.
    """

    def _explode(_name: str) -> object:
        raise RuntimeError("logfire sink is down")

    monkeypatch.setattr(events.structlog, "get_logger", _explode)

    events.emit_shaping_conversation_started(account_id=ACCOUNT, path_id=PATH)
    events.emit_change_undone(
        account_id=ACCOUNT, path_id=PATH, change_id=CHANGE, minutes_since_apply=1.0
    )


def test_a_failing_flashcards_request_path_emitter_never_breaks_the_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither of Phase 3's two request-path emitters may turn a valid write
    into a 500 (Phase 3 TDD §5.2/§5.4).

    ``review_graded`` (``services/reviews.py::grade_card``) and
    ``flashcards_kept`` (``services/flashcard_drafting.py::
    keep_flashcard_drafts``) both fire **inside an open write transaction**,
    ahead of the router's own commit — a raising sink there is a stronger
    case than 2B's own (the commit has not even landed yet), so both are
    routed through ``_emit_guarded`` too. ``flashcards_drafted`` is
    deliberately excluded: it runs from the background drafting task, so a
    raising sink there has no request and no in-flight write to break.
    """

    def _explode(_name: str) -> object:
        raise RuntimeError("logfire sink is down")

    monkeypatch.setattr(events.structlog, "get_logger", _explode)

    events.emit_review_graded(
        account_id=ACCOUNT,
        card_id=CARD,
        path_id=PATH,
        grade="got_it",
        rung_before=1,
        queue_size=10,
        queue_remaining=9,
    )
    events.emit_flashcards_kept(
        account_id=ACCOUNT,
        path_id=PATH,
        lesson_id=LESSON,
        drafted_count=4,
        kept_count=3,
    )


# --------------------------------------------------------------------------- #
# Manifest anchoring: every emitter's real field set == EVENT_FIELDS[event].
# This is what makes the metric-coverage check (test_metrics_queries) honest —
# the manifest cannot claim a field the emitter does not actually log.
# --------------------------------------------------------------------------- #


def _drive_every_emitter() -> None:
    events.emit_account_created(account_id=ACCOUNT)
    events.emit_path_created(account_id=ACCOUNT, path_id=PATH, path_level="new_to_it")
    events.emit_outline_generated(
        account_id=ACCOUNT, path_id=PATH, outcome="ready", duration_ms=1
    )
    events.emit_lesson_generated(
        account_id=ACCOUNT,
        path_id=PATH,
        lesson_id=LESSON,
        position_in_path=1,
        outcome="generated",
        duration_ms=1,
    )
    events.emit_lesson_viewed(
        account_id=ACCOUNT, path_id=PATH, lesson_id=LESSON, position_in_path=1
    )
    events.emit_quick_check_attempted(
        account_id=ACCOUNT,
        path_id=PATH,
        lesson_id=LESSON,
        position_in_path=1,
        outcome="correct",
    )
    events.emit_lesson_completed(
        account_id=ACCOUNT, path_id=PATH, lesson_id=LESSON, position_in_path=1
    )
    events.emit_path_completed(account_id=ACCOUNT, path_id=PATH, lesson_count=1)
    events.emit_path_deleted(account_id=ACCOUNT, path_id=PATH)
    events.emit_tutor_conversation_started(
        account_id=ACCOUNT, path_id=PATH, lesson_id=LESSON, position_in_path=1
    )
    events.emit_tutor_message_sent(
        account_id=ACCOUNT,
        path_id=PATH,
        lesson_id=LESSON,
        position_in_path=1,
        source="typed",
    )
    events.emit_tutor_reply_completed(
        account_id=ACCOUNT,
        path_id=PATH,
        lesson_id=LESSON,
        position_in_path=1,
        outcome="success",
        ttft_ms=1,
        duration_ms=1,
    )
    events.emit_tutor_check_shown(
        account_id=ACCOUNT, path_id=PATH, lesson_id=LESSON, position_in_path=1
    )
    events.emit_tutor_check_answered(
        account_id=ACCOUNT,
        path_id=PATH,
        lesson_id=LESSON,
        position_in_path=1,
        outcome="correct",
        first_answer=True,
    )
    events.emit_shaping_conversation_started(account_id=ACCOUNT, path_id=PATH)
    events.emit_shaping_message_sent(account_id=ACCOUNT, path_id=PATH, source="typed")
    events.emit_shaping_reply_completed(
        account_id=ACCOUNT,
        path_id=PATH,
        outcome="success",
        ttft_ms=1,
        duration_ms=1,
        has_proposal=True,
    )
    events.emit_proposal_shown(
        account_id=ACCOUNT, path_id=PATH, n_add_lessons=1, n_revisions=0, new_unit=False
    )
    events.emit_change_applied(
        account_id=ACCOUNT,
        path_id=PATH,
        change_id=CHANGE,
        n_add_lessons=1,
        n_revisions=0,
        new_unit=False,
        lesson_ids=[str(LESSON)],
    )
    events.emit_change_undone(
        account_id=ACCOUNT, path_id=PATH, change_id=CHANGE, minutes_since_apply=1.0
    )
    events.emit_flashcards_drafted(
        account_id=ACCOUNT,
        path_id=PATH,
        lesson_id=LESSON,
        position_in_path=1,
        drafted_count=4,
        outcome="generated",
        duration_ms=1,
    )
    events.emit_flashcards_kept(
        account_id=ACCOUNT,
        path_id=PATH,
        lesson_id=LESSON,
        drafted_count=4,
        kept_count=3,
    )
    events.emit_review_graded(
        account_id=ACCOUNT,
        card_id=CARD,
        path_id=PATH,
        grade="got_it",
        rung_before=1,
        queue_size=10,
        queue_remaining=9,
    )
    events.emit_beat_deployed(
        account_id=ACCOUNT,
        beat_id=BEAT,
        beat_level="some_experience",
        anchor_weekday=0,
        has_guidance=True,
    )
    events.emit_brief_research_completed(
        account_id=ACCOUNT,
        beat_id=BEAT,
        outcome="published",
        duration_ms=42_000,
        queries=6,
        documents_retrieved=10,
        documents_after_filters=8,
        findings=3,
        survivors=1,
        prompt_tokens=900,
        completion_tokens=300,
        total_tokens=1200,
    )
    events.emit_brief_read(
        account_id=ACCOUNT, beat_id=BEAT, brief_id=BRIEF, marker="opened", age_days=2
    )


def test_manifest_matches_real_emission(recorder: _Recorder) -> None:
    _drive_every_emitter()

    emitted = {event: set(fields) for event, fields in recorder.records}

    # Every declared event was driven, and each emitter's real field set is
    # exactly what the manifest declares (no drift in either direction).
    assert set(emitted) == set(events.EVENT_FIELDS)
    for event, fields in events.EVENT_FIELDS.items():
        assert emitted[event] == set(fields), event
