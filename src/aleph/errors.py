"""Shared API error envelope helpers.

Ported from habagou so every API error — the AL-020 ``401`` gate included —
speaks one shape from day one: ``{"error": {code, message, request_id}}``.
AL-050/051 (route protection) and the frontend consume this envelope, so it is
established here rather than retrofitted per-router later.
"""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


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
