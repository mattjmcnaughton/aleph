"""Shared API error envelope helpers.

Ported from habagou so every API error — the AL-020 ``401`` gate included —
speaks one shape from day one: ``{"error": {code, message, request_id}}``.
AL-050/051 (route protection) and the frontend consume this envelope, so it is
established here rather than retrofitted per-router later.
"""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorDetail(BaseModel):
    """The body of the envelope: what failed, in a code and in words."""

    #: Machine-readable: ``not_found``, ``conflict``, ``validation_error``, ...
    code: str
    #: Human-readable copy; never parsed by a client.
    message: str
    #: Echoes the ``X-Request-ID`` response header, for support.
    request_id: str
    #: Present only when there is more to say, e.g. ``{"reason": ...}`` on a
    #: ``409`` or the field errors on a ``422``.
    details: Any | None = None


class ErrorEnvelope(BaseModel):
    """Every API error's body (iOS TDD §4.1): ``{"error": {...}}``.

    Declared as the ``default`` response of every ``/api/v1`` operation
    (``app.py``), so a generated client can decode it. :func:`error_response`
    builds the same shape; a unit test validates its output against this model.
    """

    error: ErrorDetail


#: The ``responses=`` metadata that makes every non-declared status the envelope.
#: Using ``default`` also stops FastAPI documenting its own
#: ``422 HTTPValidationError`` body, which this app never sends.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    "default": {"model": ErrorEnvelope, "description": "Error envelope"},
}


def error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
) -> JSONResponse:
    """Build the canonical API error envelope."""
    request_id = getattr(request.state, "request_id", "")
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": request_id,
    }
    if details is not None:
        error["details"] = details
    content: dict[str, Any] = {
        "error": error,
    }
    return JSONResponse(content, status_code=status_code)
