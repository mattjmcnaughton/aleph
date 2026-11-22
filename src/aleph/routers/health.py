"""Health check endpoints."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from aleph.config import settings

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict:
    """Liveness probe, with the commit the running build came from."""
    return {"status": "ok", "commit": settings.git_sha}


@router.get("/readyz", response_model=None)
async def readyz() -> dict[str, str] | JSONResponse:
    """Readiness probe, with the commit the running build came from."""
    from sqlalchemy import text

    from aleph.db import async_session

    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            {
                "status": "not ready",
                "database": "unreachable",
                "commit": settings.git_sha,
            },
            status_code=503,
        )
    return {"status": "ready", "commit": settings.git_sha}
