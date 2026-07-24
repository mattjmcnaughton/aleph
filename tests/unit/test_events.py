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


def test_manifest_matches_real_emission(recorder: _Recorder) -> None:
    _drive_every_emitter()

    emitted = {event: set(fields) for event, fields in recorder.records}

    # Every declared event was driven, and each emitter's real field set is
    # exactly what the manifest declares (no drift in either direction).
    assert set(emitted) == set(events.EVENT_FIELDS)
    for event, fields in events.EVENT_FIELDS.items():
        assert emitted[event] == set(fields), event
