"""Smoke tests proving the app wiring and test harness work."""

import asyncio

import pytest

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


def test_production_app_exposes_no_e2e_route() -> None:
    """D11 (Phase 5 TDD §11): the e2e clock router never reaches production.

    ``scripts/e2e_backend.py`` mounts ``POST /__e2e__/shift-completions`` onto
    ``create_stub_app``'s app *after* calling :func:`create_app` — never inside
    ``create_app`` itself, and ``aleph.app`` imports nothing from
    ``scripts.e2e_backend`` (grep the module: the only reference to
    ``scripts`` anywhere under ``src/aleph`` is in this docstring). That is
    the "never imported" guarantee TDD §11 asks for; what this test pins is
    its externally-visible consequence — building the app :func:`create_app`
    actually ships with produces no route under ``/__e2e__`` at all, on a
    fresh app object this test builds itself (never the module-level ``app``
    singleton some other test may have already mutated).
    """
    app = create_app()
    paths = app.openapi()["paths"]
    assert not any(path.startswith("/__e2e__") for path in paths)
    assert not any(
        str(getattr(route, "path", "")).startswith("/__e2e__")
        for route in app.router.routes
    )


@pytest.mark.anyio
async def test_e2e_retrieval_gate_holds_only_its_topic_and_preserves_failure() -> None:
    from aleph.services.retrieval import RetrievalUnavailableError
    from scripts.e2e_backend import ControlledRetriever

    retriever = ControlledRetriever()
    topic = "[force-retrieval-failure] held topic"
    retriever.hold(topic)
    pending = asyncio.create_task(retriever.search([f"{topic} news"]))
    try:
        await asyncio.sleep(0)
        assert not pending.done()
        assert await retriever.search(["unrelated topic news"])
        retriever.release(topic)
        with pytest.raises(RetrievalUnavailableError):
            await asyncio.wait_for(pending, timeout=1)
        # Release removes the gate; later runs cannot hang behind stale state.
        with pytest.raises(RetrievalUnavailableError):
            await asyncio.wait_for(retriever.search([topic]), timeout=1)
    finally:
        pending.cancel()
