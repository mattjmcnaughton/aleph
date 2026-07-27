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
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app-abc123.js").write_text(BUNDLE)
    monkeypatch.setattr(serve, "FRONTEND_DIST", tmp_path)
    return TestClient(app_module.create_app())


def test_root_serves_the_shell(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert '<div id="root">' in response.text


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


def test_probes_are_untouched(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_non_get_request_does_not_fall_back(client: TestClient) -> None:
    """Only navigations fall back; a stray write must not answer ``200`` + HTML."""
    response = client.post("/paths/e185b126-b796-4e2d-96cb-f09f0944875b")

    assert response.status_code != 200
    assert '<div id="root">' not in response.text
