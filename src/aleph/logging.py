"""Structured logging configuration."""

import logging

import logfire
import structlog

from aleph.config import settings


def configure_logging() -> None:
    """Configure structlog for the application."""
    log_level = logging.getLevelNamesMapping().get(
        settings.log_level.upper(), logging.INFO
    )
    renderer = (
        structlog.dev.ConsoleRenderer()
        if settings.log_format == "console"
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.TimeStamper(fmt="iso"),
            # Attach Logfire to the pipeline (TDD §9): every structured log
            # event is emitted to Logfire with its fields intact, landing in the
            # same sink as spans. A clean no-op when Logfire has no token. Placed
            # just before the final renderer so it sees the fully-built event
            # dict but the console/JSON renderer still formats stdout output.
            logfire.StructlogProcessor(),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
