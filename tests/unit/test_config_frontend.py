"""The built-SPA location setting (``settings.frontend_dist_dir``, AL-600).

The web app lives at ``clients/web/`` (iOS TDD §15), outside the Python package,
so the production mount can no longer find ``dist/`` relative to ``serve.py``.
The location is a setting instead: ``FRONTEND_DIST_DIR`` in the image, and the
checkout-relative ``clients/web/dist`` by default — resolved from the repository
root, so the default does not depend on the process's working directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from starlette.routing import Mount

from aleph.config import Settings
from aleph.web import serve

if TYPE_CHECKING:
    import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _frontend_mounts(app: FastAPI) -> list[Path]:
    """The directory each mounted SPA serves, in mount order."""
    return [
        Path(route.app.directory)  # ty: ignore[unresolved-attribute]
        for route in app.routes
        if isinstance(route, Mount) and route.name == "frontend"
    ]


def test_default_is_the_checkout_relative_web_dist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A different working directory must not move the default (and an empty one
    # holds no ``.env`` to leak a developer's own value in).
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FRONTEND_DIST_DIR", raising=False)

    settings = Settings()

    assert settings.frontend_dist_dir == REPO_ROOT / "clients" / "web" / "dist"
    assert settings.frontend_dist_dir.is_absolute()


def test_env_var_overrides_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FRONTEND_DIST_DIR", str(tmp_path / "dist"))

    settings = Settings()

    assert settings.frontend_dist_dir == tmp_path / "dist"


def test_mount_reads_the_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "index.html").write_text("<div id='root'></div>")
    monkeypatch.setattr(serve.settings, "frontend_dist_dir", tmp_path)
    app = FastAPI()

    serve.mount_frontend(app)

    assert _frontend_mounts(app) == [tmp_path]


def test_missing_dist_is_not_mounted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(serve.settings, "frontend_dist_dir", tmp_path / "absent")
    app = FastAPI()

    serve.mount_frontend(app)

    assert _frontend_mounts(app) == []
