"""FastAPI application factory."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from aleph.logging import configure_logging
from aleph.routers import health
from aleph.telemetry import setup_telemetry
from aleph.web.serve import mount_frontend

DESCRIPTION = "Mobile-friendly AI tutor: name a topic, get a generated learning path"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    configure_logging()
    yield


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="aleph",
        description=DESCRIPTION,
        version="0.1.0",
        lifespan=lifespan,
    )

    setup_telemetry(app)

    app.include_router(health.router)

    # Mount frontend static files (only serves if dist/ exists)
    mount_frontend(app)

    return app


app = create_app()
