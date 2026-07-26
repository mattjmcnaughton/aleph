"""Structured logging configuration."""

import logging
import sys
import types
from collections.abc import MutableMapping
from typing import Any

import logfire
import structlog

from aleph.config import settings

_REDACTED = "[REDACTED]"
_SENSITIVE_FIELDS = frozenset(
    {
        "api_key",
        "authorization",
        "client_secret",
        "cookie",
        "database_url",
        "id_token",
        "logfire_token",
        "oidc_client_secret",
        "openrouter_api_key",
        "password",
        "passwd",
        "proxy_authorization",
        "refresh_token",
        "secret",
        "secret_key",
        "session_secret_key",
        "set_cookie",
        "token",
    }
)
_SENSITIVE_SUFFIXES = (
    "_access_token",
    "_api_key",
    "_cookie",
    "_id_token",
    "_password",
    "_refresh_token",
    "_secret",
    "_secret_key",
)
_CONFIGURED_SECRET_FIELDS = (
    "logfire_token",
    "oidc_client_secret",
    "openrouter_api_key",
    "session_secret_key",
)


def _normalise_field_name(field: object) -> str:
    return str(field).strip().lower().replace("-", "_").replace(" ", "_")


def _is_sensitive_field(field: object) -> bool:
    normalised = _normalise_field_name(field)
    return normalised in _SENSITIVE_FIELDS or normalised.endswith(_SENSITIVE_SUFFIXES)


def _redact_configured_secrets(value: str) -> str:
    redacted = value
    for field in _CONFIGURED_SECRET_FIELDS:
        secret = getattr(settings, field)
        if secret:
            redacted = redacted.replace(secret, _REDACTED)
    return redacted


def _redact_value(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _REDACTED if _is_sensitive_field(key) else _redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    if isinstance(value, str):
        return _redact_configured_secrets(value)
    return value


def _redact_credentials(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Remove credential-shaped structured fields before any logging sink."""
    return {
        key: _REDACTED if _is_sensitive_field(key) else _redact_value(value)
        for key, value in event_dict.items()
    }


def _add_safe_exception_context(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Add no-locals exception metadata that remains useful if a sink scrubs text."""
    exc_info = event_dict.get("exc_info")
    if exc_info is True:
        exc_info = sys.exc_info()
    if not isinstance(exc_info, tuple) or len(exc_info) != 3:
        return event_dict

    error_type, _error, traceback = exc_info
    if not isinstance(error_type, type):
        return event_dict

    event_dict["error_type"] = f"{error_type.__module__}.{error_type.__qualname__}"
    stack: list[str] = []
    while isinstance(traceback, types.TracebackType):
        code = traceback.tb_frame.f_code
        stack.append(f"{code.co_filename}:{traceback.tb_lineno} in {code.co_name}")
        traceback = traceback.tb_next
    event_dict["error_stack"] = stack
    return event_dict


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
            # Preserve type + frame locations separately from exception text.
            # Logfire may scrub an entire traceback that contains a sensitive
            # substring; this metadata remains useful and never reads locals.
            _add_safe_exception_context,
            # Convert ``exc_info`` to a plain traceback before any sink sees it.
            # The standard renderer keeps frames and the exception message but
            # never captures frame-local values.
            structlog.processors.ExceptionRenderer(),
            # Scrub structured credentials before stdout or Logfire can observe
            # them, including configured secrets copied into an SDK error message.
            # Exact/suffix matching avoids hiding benign metrics such as
            # ``prompt_tokens`` and the domain's fencing token.
            _redact_credentials,
            # Attach Logfire to the pipeline (TDD §9): every structured log
            # event is emitted to Logfire with its fields intact, landing in the
            # same sink as spans. A clean no-op when Logfire has no token. It sees
            # the sanitized traceback and fields, never raw ``exc_info`` or
            # credential-shaped values.
            logfire.StructlogProcessor(),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
