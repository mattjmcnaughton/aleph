"""Health check endpoints."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict:
    """Liveness probe."""
    return {"status": "ok"}


@router.get("/readyz", response_model=None)
async def readyz() -> dict[str, str] | JSONResponse:
    """Readiness probe."""
    from sqlalchemy import text

    from aleph.db import async_session

    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            {"status": "not ready", "database": "unreachable"}, status_code=503
        )
    return {"status": "ready"}
