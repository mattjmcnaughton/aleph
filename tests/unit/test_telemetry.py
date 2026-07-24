"""Unit tests for Logfire telemetry configuration (AL-005, TDD §9 / D11).

Two layers of evidence:

* Configuration shape — the *critical* no-token property (dev/CI default): with
  no ``LOGFIRE_TOKEN`` and no OTLP endpoint, ``logfire.configure`` is called
  with ``send_to_logfire="if-token-present"``, ``token=None`` and an empty
  span-processor list, so nothing attempts a network connection (no OTLP
  connection-refused spam, AL-003).
* Real spans — using Logfire's own in-memory test exporter (``capfire``), a
  FastAPI request routed through the actual ``setup_telemetry`` produces a
  request span, and SQLAlchemy queries produce query spans. This is what a
  token-holder sees in Logfire, verified offline with ``send_to_logfire=False``.
"""

import logfire
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from starlette.testclient import TestClient

import aleph.telemetry as telemetry

# ``capfire`` is provided by Logfire's pytest11 plugin (no import needed): it
# configures Logfire with an in-memory span exporter and ``send_to_logfire=False``.


def test_configure_is_noop_without_token(monkeypatch) -> None:
    """No token + no OTLP endpoint => configure with zero exporters (no network)."""
    recorded: dict = {}

    monkeypatch.setattr(telemetry, "_logfire_configured", False)
    monkeypatch.setattr(telemetry.settings, "logfire_token", "")
    monkeypatch.setattr(telemetry.settings, "otel_exporter_otlp_endpoint", "")
    monkeypatch.setattr(telemetry.logfire, "configure", lambda **k: recorded.update(k))
    monkeypatch.setattr(
        telemetry.logfire, "instrument_sqlalchemy", lambda *a, **k: None
    )
    monkeypatch.setattr(
        telemetry.logfire, "instrument_pydantic_ai", lambda *a, **k: None
    )
    monkeypatch.setattr(telemetry.logfire, "instrument_httpx", lambda *a, **k: None)

    telemetry._configure_logfire()

    assert recorded["send_to_logfire"] == "if-token-present"
    assert recorded["token"] is None
    assert recorded["service_name"] == "aleph"
    assert recorded["console"] is False
    # Empty processor list == no OTLP exporter == nothing dials the network.
    assert recorded["additional_span_processors"] == []


def test_configure_passes_token_and_otlp_endpoint_when_set(monkeypatch) -> None:
    """A token flows to ``configure``; an OTLP endpoint adds a span processor."""
    recorded: dict = {}

    monkeypatch.setattr(telemetry, "_logfire_configured", False)
    monkeypatch.setattr(telemetry.settings, "logfire_token", "pylf_live_xxx")
    monkeypatch.setattr(
        telemetry.settings, "otel_exporter_otlp_endpoint", "http://collector:4318"
    )
    monkeypatch.setattr(telemetry.logfire, "configure", lambda **k: recorded.update(k))
    monkeypatch.setattr(
        telemetry.logfire, "instrument_sqlalchemy", lambda *a, **k: None
    )
    monkeypatch.setattr(
        telemetry.logfire, "instrument_pydantic_ai", lambda *a, **k: None
    )
    monkeypatch.setattr(telemetry.logfire, "instrument_httpx", lambda *a, **k: None)

    telemetry._configure_logfire()

    assert recorded["token"] == "pylf_live_xxx"
    assert len(recorded["additional_span_processors"]) == 1


def test_configure_is_process_wide(monkeypatch) -> None:
    """``_configure_logfire`` is idempotent across the process."""
    calls: list[dict] = []

    monkeypatch.setattr(telemetry, "_logfire_configured", False)
    monkeypatch.setattr(telemetry.settings, "logfire_token", "test-token")
    monkeypatch.setattr(telemetry.settings, "otel_exporter_otlp_endpoint", "")
    monkeypatch.setattr(telemetry.logfire, "configure", lambda **k: calls.append(k))
    monkeypatch.setattr(
        telemetry.logfire, "instrument_sqlalchemy", lambda *a, **k: None
    )
    monkeypatch.setattr(
        telemetry.logfire, "instrument_pydantic_ai", lambda *a, **k: None
    )
    monkeypatch.setattr(telemetry.logfire, "instrument_httpx", lambda *a, **k: None)

    telemetry._configure_logfire()
    telemetry._configure_logfire()

    assert len(calls) == 1
    assert calls[0]["token"] == "test-token"


def test_setup_telemetry_instruments_each_app(monkeypatch) -> None:
    """Each app is FastAPI-instrumented; configuration runs once per call."""
    configured: list[bool] = []
    instrumented: list[FastAPI] = []

    monkeypatch.setattr(
        telemetry, "_configure_logfire", lambda: configured.append(True)
    )
    monkeypatch.setattr(
        telemetry.logfire, "instrument_fastapi", lambda app: instrumented.append(app)
    )

    first, second = FastAPI(), FastAPI()
    telemetry.setup_telemetry(first)
    telemetry.setup_telemetry(second)

    assert len(configured) == 2
    assert instrumented == [first, second]


def test_setup_telemetry_produces_fastapi_request_spans(capfire, monkeypatch) -> None:
    """A request through ``setup_telemetry`` produces a captured FastAPI span.

    ``capfire`` owns the Logfire config (an in-memory exporter,
    ``send_to_logfire=False``); we mark Logfire already-configured so
    ``setup_telemetry`` keeps that exporter and only instruments the app.
    """
    monkeypatch.setattr(telemetry, "_logfire_configured", True)

    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    telemetry.setup_telemetry(app)

    with TestClient(app) as client:
        assert client.get("/ping").status_code == 200

    names = [span["name"] for span in capfire.exporter.exported_spans_as_dict()]
    assert "GET /ping" in names, names


def test_sqlalchemy_instrumentation_produces_query_spans(capfire) -> None:
    """``logfire.instrument_sqlalchemy`` (what telemetry uses) emits query spans."""
    engine = create_engine("sqlite://")
    logfire.instrument_sqlalchemy(engine=engine)

    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    names = [span["name"] for span in capfire.exporter.exported_spans_as_dict()]
    assert "SELECT" in names, names
