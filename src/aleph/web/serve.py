"""Static file serving for the frontend in production."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"


def mount_frontend(app: FastAPI) -> None:
    """Mount the frontend static files if the dist directory exists.

    In production, the frontend is built to `web/frontend/dist/` and served
    as static files. In development, the frontend dev server runs separately.
    """
    if FRONTEND_DIST.is_dir():
        app.mount(
            "/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend"
        )
