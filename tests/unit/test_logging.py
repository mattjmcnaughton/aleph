"""Unit tests for the structlog + Logfire pipeline (AL-005, TDD §9).

structlog is the application logging front end (habagou's pattern verbatim:
contextvars merge, ISO timestamps, JSON in prod / console in dev). Aleph adds
one thing on top: ``logfire.StructlogProcessor`` in the pipeline so every
structured log event lands in Logfire with its fields intact — the same sink as
spans, giving one vocabulary from PRD workflows (§12) to production traces.
"""

import json

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


def test_exception_log_keeps_traceback_without_frame_locals(capsys) -> None:
    """Exceptions stay debuggable without serialising local credentials."""
    configure_logging()
    credential = "openrouter-secret-must-not-appear"

    try:
        raise RuntimeError("provider request failed")
    except RuntimeError:
        structlog.get_logger("aleph.test").exception("outline_generation_failed")

    rendered = capsys.readouterr().out
    assert "Traceback (most recent call last)" in rendered
    assert "RuntimeError: provider request failed" in rendered
    assert credential not in rendered


def test_structured_credentials_are_redacted_without_hiding_token_metrics(
    capsys,
) -> None:
    """Credential fields are removed recursively; usage metrics stay useful."""
    configure_logging()

    structlog.get_logger("aleph.test").info(
        "provider_request",
        authorization="Bearer sensitive-access-token",
        request={
            "api_key": "sensitive-api-key",
            "cookie": "session=sensitive-cookie",
            "total_tokens": 42,
        },
    )

    record = json.loads(capsys.readouterr().out)
    assert record["authorization"] == "[REDACTED]"
    assert record["request"]["api_key"] == "[REDACTED]"
    assert record["request"]["cookie"] == "[REDACTED]"
    assert record["request"]["total_tokens"] == 42


def test_configured_secrets_are_removed_from_exception_messages(
    capsys, monkeypatch
) -> None:
    """Known credentials cannot escape when an SDK includes one in an error."""
    from aleph.config import settings

    credential = "sk-or-v1-sensitive-openrouter-credential"
    monkeypatch.setattr(settings, "openrouter_api_key", credential)
    configure_logging()

    try:
        raise RuntimeError(f"provider rejected credential {credential}")
    except RuntimeError:
        structlog.get_logger("aleph.test").exception("outline_generation_failed")

    rendered = capsys.readouterr().out
    assert credential not in rendered
    assert "provider rejected credential [REDACTED]" in rendered


def test_logfire_receives_sanitized_exception_context(capfire, monkeypatch) -> None:
    """The remote sink retains the failure while never receiving the secret."""
    from aleph.config import settings

    credential = "sensitive-oidc-client-secret"
    monkeypatch.setattr(settings, "oidc_client_secret", credential)
    configure_logging()

    try:
        raise RuntimeError(f"OIDC exchange failed with {credential}")
    except RuntimeError:
        structlog.get_logger("aleph.test").exception("oidc_exchange_failed")

    failed = next(
        span
        for span in capfire.exporter.exported_spans_as_dict()
        if span["name"] == "oidc_exchange_failed"
    )
    exported = json.dumps(failed)
    assert credential not in exported
    assert failed["attributes"]["error_type"] == "builtins.RuntimeError"
    assert "test_logfire_receives_sanitized_exception_context" in exported
