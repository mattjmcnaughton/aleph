"""Smoke tests proving the app wiring and test harness work."""

import asyncio

from aleph.app import create_app
from aleph.routers.health import healthz


def test_app_factory_builds() -> None:
    app = create_app()
    assert app.title == "aleph"
    paths = app.openapi()["paths"]
    assert "/healthz" in paths
    assert "/readyz" in paths


def test_healthz_returns_ok() -> None:
    assert asyncio.run(healthz()) == {"status": "ok"}
