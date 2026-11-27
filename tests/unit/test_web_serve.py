"""Unit tests for the production SPA mount (``aleph.web.serve``).

The frontend is a client-side router, so ``/paths/<id>`` is a route in the
browser and not a file in ``dist/``. Only the production image serves ``dist/``
— locally and in the e2e harness the vite dev server answers, and its own
history fallback hides the gap entirely — so these tests are the regression
fence for deep-link refreshes.

The mount is exercised through the real ``create_app()`` (with ``FRONTEND_DIST``
pointed at a fixture tree) rather than a bare ``StaticFiles`` instance, because
half of what matters is the *interaction*: the SPA fallback must not swallow the
404s the API routers and the error envelope are responsible for.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from aleph import app as app_module
from aleph.web import serve

if TYPE_CHECKING:
    from pathlib import Path

SHELL = '<!doctype html><html><body><div id="root"></div></body></html>'
BUNDLE = "console.log('app');"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The real app, serving a minimal stand-in for a built frontend."""
    (tmp_path / "index.html").write_text(SHELL)
    (tmp_path / "manifest.webmanifest").write_text('{"name":"aleph"}')
    (tmp_path / "sw.js").write_text("// No caching.\n")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app-abc123.js").write_text(BUNDLE)
    monkeypatch.setattr(serve, "FRONTEND_DIST", tmp_path)
    return TestClient(app_module.create_app())


def test_root_serves_the_shell(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert '<div id="root">' in response.text


def test_install_resources(client: TestClient) -> None:
    manifest = client.get("/manifest.webmanifest")
    assert manifest.status_code == 200
    assert manifest.headers["content-type"] == "application/manifest+json"
    assert manifest.json() == {"name": "aleph"}
    worker = client.get("/sw.js")
    assert worker.status_code == 200
    assert worker.headers["cache-control"] == "no-cache"
    assert client.head("/sw.js").headers["cache-control"] == "no-cache"


@pytest.mark.parametrize(
    "path", ["/missing.webmanifest", "/missing-sw.js", "/icons/missing.png"]
)
def test_missing_install_resources(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 404
    assert '<div id="root">' not in response.text


@pytest.mark.parametrize(
    "route",
    [
        "/paths/e185b126-b796-4e2d-96cb-f09f0944875b",
        "/lessons/e185b126-b796-4e2d-96cb-f09f0944875b",
        "/new",
        "/login",
    ],
)
def test_deep_link_serves_the_shell(client: TestClient, route: str) -> None:
    """A refresh or a shared link on a client-side route must reach the router.

    Plain ``StaticFiles(html=True)`` 404s these — ``html=True`` only resolves
    *directory* index files — which is what made an in-app navigation work while
    a refresh died on the JSON error envelope.
    """
    response = client.get(route)

    assert response.status_code == 200
    assert '<div id="root">' in response.text


def test_real_assets_are_still_served(client: TestClient) -> None:
    response = client.get("/assets/app-abc123.js")

    assert response.status_code == 200
    assert response.text == BUNDLE


def test_missing_asset_is_a_real_404(client: TestClient) -> None:
    """A missing *file* must not fall back to the shell.

    Answering a stale bundle reference with ``200`` + HTML turns an obvious
    failed request into a blank page and a console parse error.
    """
    response = client.get("/assets/app-stale999.js")

    assert response.status_code == 404
    assert '<div id="root">' not in response.text


@pytest.mark.parametrize(
    "route",
    ["/api/v1/nonexistent", "/api/v1/paths/nope/nope", "/auth/nonexistent"],
)
def test_unknown_backend_path_keeps_the_error_envelope(
    client: TestClient, route: str
) -> None:
    """The backend's own 404s must stay JSON.

    Falling back here would answer a typo'd API path with ``200`` + HTML, so
    every client error handler would see a parse failure instead of the 404 it
    was written for.
    """
    response = client.get(route)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    # A write too: ``StaticFiles`` would otherwise answer it with its own 405.
    written = client.post(route, json={})
    assert written.status_code == 404
    assert written.json()["error"]["code"] == "not_found"


def test_route_added_after_create_app_is_reachable(client: TestClient) -> None:
    """The shell is a fallback, not a route that shadows later ones.

    A ``Mount("/")`` matches every path, so any route registered after
    ``create_app()`` (as the integration suite's ``_authprobe`` is) sat behind
    it and answered the SPA's 404 whenever a built ``dist/`` was present.
    """
    app = client.app
    assert isinstance(app, FastAPI)

    @app.get("/api/v1/_late")
    async def _late() -> dict[str, bool]:
        return {"ok": True}

    assert client.get("/api/v1/_late").json() == {"ok": True}


def test_probes_are_untouched(client: TestClient) -> None:
    assert client.get("/healthz").json()["status"] == "ok"


def test_non_get_request_does_not_fall_back(client: TestClient) -> None:
    """Only navigations fall back; a stray write must not answer ``200`` + HTML."""
    response = client.post("/paths/e185b126-b796-4e2d-96cb-f09f0944875b")

    assert response.status_code != 200
    assert '<div id="root">' not in response.text


def test_the_oauth_consent_page_refuses_to_be_framed(client: TestClient) -> None:
    connect = client.get("/connect")
    assert connect.status_code == 200
    assert '<div id="root">' in connect.text
    assert connect.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert connect.headers["x-frame-options"] == "DENY"
    # Only the consent page: other screens are unchanged.
    assert "content-security-policy" not in client.get("/paths/abc").headers
