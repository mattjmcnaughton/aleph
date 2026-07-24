"""Logfire (OpenTelemetry) telemetry setup (TDD §9 / D11).

Logfire is the single telemetry sink: FastAPI request spans, SQLAlchemy query
spans, httpx client spans, and Pydantic AI model-call spans (token/cost) all
flow to it, and structlog events join them via ``logging.py``'s
``StructlogProcessor``. It is OTel under the hood, matching the scaffold's
``enable_otel`` seam.

The **critical property** (dev/CI default) is a clean no-op when
``LOGFIRE_TOKEN`` is unset: ``send_to_logfire="if-token-present"`` means no
Logfire exporter is created, and with no ``OTEL_EXPORTER_OTLP_ENDPOINT`` no OTLP
exporter is created either — so nothing dials the network (no
connection-refused spam, AL-003). Instrumentation still runs; spans are simply
created and dropped.
"""

import logfire
from fastapi import FastAPI
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from aleph import db
from aleph.config import settings

_logfire_configured = False


def setup_telemetry(app: FastAPI) -> None:
    """Configure process-wide telemetry and instrument this FastAPI app."""
    _configure_logfire()
    logfire.instrument_fastapi(app)


def _configure_logfire() -> None:
    """Configure Logfire and non-app instrumentation once per process.

    Idempotent: FastAPI instrumentation is per-app (in ``setup_telemetry``), but
    ``logfire.configure`` and the global SQLAlchemy/httpx/Pydantic AI
    instrumentation must run exactly once.
    """
    global _logfire_configured  # noqa: PLW0603 - process-wide SDK configuration
    if _logfire_configured:
        return

    span_processors = []
    if settings.otel_exporter_otlp_endpoint:
        exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
        span_processors.append(BatchSpanProcessor(exporter))

    logfire.configure(
        send_to_logfire="if-token-present",
        token=settings.logfire_token or None,
        service_name="aleph",
        console=False,
        additional_span_processors=span_processors,
    )
    # Instrument the module-level async engine (and future SQLAlchemy engines).
    logfire.instrument_sqlalchemy(engine=db.engine)
    # httpx covers OpenRouter/OIDC calls not otherwise wrapped by an agent.
    logfire.instrument_httpx()
    # Conversation content is intentionally retained for reviewing generation
    # quality in Logfire, including replayed history, tool calls, and responses.
    logfire.instrument_pydantic_ai(include_content=True)
    _logfire_configured = True
