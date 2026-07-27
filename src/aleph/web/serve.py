"""Static file serving for the frontend in production."""

from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"

# Path prefixes the backend owns. A request under one of these must never fall
# back to the SPA shell: a typo'd or retired API path has to answer with the
# JSON error envelope (`errors.error_response`), not `200` + HTML, or every
# client-side error handler sees a parse failure instead of the 404 it was
# written for.
BACKEND_PREFIXES = frozenset({"api", "auth", "healthz", "readyz"})


class SPAStaticFiles(StaticFiles):
    """``StaticFiles`` with a single-page-app history fallback.

    The frontend is a client-side router (TanStack Router), so ``/paths/<id>``
    is a route in the browser and not a file in ``dist/``. Plain ``StaticFiles``
    404s such a URL even with ``html=True`` — that flag only resolves *directory*
    index files — so an in-app navigation worked while a refresh or a shared deep
    link died on the JSON error envelope. Serving the shell instead lets the
    router resolve the route on the client, which is what the vite dev server's
    own history fallback does in development (and why neither `just dev` nor the
    Playwright harness, both of which run that dev server, ever saw this).
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or not _is_spa_navigation(path, scope):
                raise
            return await super().get_response("index.html", scope)


def _is_spa_navigation(path: str, scope: Scope) -> bool:
    """Whether a miss on ``path`` should render the SPA shell.

    Deliberately narrow: only a document request for a route the client router
    could own. Everything else keeps the 404 it earned.
    """
    if scope.get("method") not in ("GET", "HEAD"):
        return False
    if path.split("/", 1)[0] in BACKEND_PREFIXES:
        return False
    # A missing *file* is a real 404. Falling back would answer a stale bundle
    # reference with `200` + HTML, turning an obvious failed request into a
    # blank page and a console parse error.
    return "." not in path.rsplit("/", 1)[-1]


def mount_frontend(app: FastAPI) -> None:
    """Mount the frontend static files if the dist directory exists.

    In production, the frontend is built to `web/frontend/dist/` and served
    as static files. In development, the frontend dev server runs separately.
    """
    if FRONTEND_DIST.is_dir():
        app.mount(
            "/",
            SPAStaticFiles(directory=str(FRONTEND_DIST), html=True),
            name="frontend",
        )
