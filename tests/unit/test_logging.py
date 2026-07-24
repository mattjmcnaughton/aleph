"""Unit tests for the structlog + Logfire pipeline (AL-005, TDD §9).

structlog is the application logging front end (habagou's pattern verbatim:
contextvars merge, ISO timestamps, JSON in prod / console in dev). Aleph adds
one thing on top: ``logfire.StructlogProcessor`` in the pipeline so every
structured log event lands in Logfire with its fields intact — the same sink as
spans, giving one vocabulary from PRD workflows (§12) to production traces.
"""

import structlog

from aleph.logging import configure_logging

# ``capfire`` is provided by Logfire's pytest11 plugin (no import needed): it
# configures Logfire with an in-memory span exporter and ``send_to_logfire=False``.


def test_structlog_events_carry_structured_fields_into_logfire(capfire) -> None:
    """A structlog event surfaces in Logfire as a log record with its fields."""
    configure_logging()

    structlog.get_logger("aleph.test").info(
        "path_created", path_id="abc-123", workflow="W1"
    )

    logs = [
        span
        for span in capfire.exporter.exported_spans_as_dict()
        if span["attributes"].get("logfire.span_type") == "log"
    ]
    created = next((s for s in logs if s["name"] == "path_created"), None)
    assert created is not None, logs

    attributes = created["attributes"]
    assert attributes["path_id"] == "abc-123"
    assert attributes["workflow"] == "W1"


def test_configure_logging_installs_logfire_processor() -> None:
    """The Logfire structlog processor is present in the configured pipeline."""
    import logfire

    configure_logging()
    processors = structlog.get_config()["processors"]
    assert any(isinstance(p, logfire.StructlogProcessor) for p in processors)
