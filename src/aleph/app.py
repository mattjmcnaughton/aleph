"""FastAPI application factory."""

import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from aleph.auth import register_provider
from aleph.config import settings
from aleph.errors import error_response
from aleph.logging import configure_logging
from aleph.routers import auth, health
from aleph.routers.v1 import beats as v1_beats
from aleph.routers.v1 import feature_flags as v1_feature_flags
from aleph.routers.v1 import flashcards as v1_flashcards
from aleph.routers.v1 import lessons as v1_lessons
from aleph.routers.v1 import paths as v1_paths
from aleph.routers.v1 import progress as v1_progress
from aleph.routers.v1 import shaping as v1_shaping
from aleph.routers.v1 import tutor as v1_tutor
from aleph.services.generation import generation_orchestrator
from aleph.services.lifecycle import GenerationLifecycle
from aleph.telemetry import setup_telemetry
from aleph.web.serve import mount_frontend

DESCRIPTION = "Mobile-friendly AI tutor: name a topic, get a generated learning path"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    Starts the generation lifecycle (AL-041, §5.4): it binds the module-level
    orchestrator's runtime seams (the task registry + the concurrency semaphore)
    and launches the in-process reconciler loop, then tears them down on
    shutdown — cancelling in-flight generation so its rows revert via stale
    recovery and stay re-claimable.
    """
    configure_logging()
    lifecycle = GenerationLifecycle(generation_orchestrator, config=settings)
    await lifecycle.start()
    app.state.generation_lifecycle = lifecycle
    try:
        yield
    finally:
        await lifecycle.stop()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    register_provider(settings)
    app = FastAPI(
        title="aleph",
        description=DESCRIPTION,
        version="0.1.0",
        lifespan=lifespan,
    )

    # First-party signed session cookie (TDD §7/D2): HttpOnly, SameSite=Lax,
    # holding only the local user UUID. ``https_only`` (Secure) is on in prod.
    # Starlette's default ``max_age`` (14 days) is inherited deliberately
    # (habagou parity): the cookie is a signed bearer with no server-side
    # revocation, so logout clears it client-side and a leaked cookie stays
    # valid until it expires or the signing secret rotates — an accepted MVP
    # trade-off, revisited if session invalidation becomes a requirement.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret_key,
        same_site="lax",
        https_only=settings.session_cookie_secure,
    )

    setup_telemetry(app)
    _install_request_id(app)
    _install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(v1_paths.router)
    app.include_router(v1_lessons.router)
    app.include_router(v1_feature_flags.router)
    # Every route on this one is hidden behind the ``tutor`` feature flag
    # (404 when it resolves off), so mounting it is safe in production while
    # Phase 2 is still being built (epic #82, owner amendment 1).
    app.include_router(v1_tutor.router)
    # Likewise behind the ``shaping`` flag (epic #114, adopted convention 1) —
    # a separate key, so Phase 2B can ship dark and be killed without
    # disturbing the already-launched in-lesson tutor.
    app.include_router(v1_shaping.router)
    # Likewise behind the ``streaks`` flag (Phase 5 TDD D7) — its own key, so
    # this slice can ship dark and be killed without disturbing either
    # already-launched surface above.
    app.include_router(v1_progress.router)
    # Likewise behind the ``flashcards`` flag (Phase 3 TDD D10) — its own key,
    # so this slice can ship dark (drafting, the queue, review, and the due
    # pill all 404 by default) and be dogfooded by admins without disturbing
    # any already-launched surface above.
    app.include_router(v1_flashcards.router)
    # Likewise behind the ``analyst`` flag (Phase 6 TDD D12) — its own key,
    # so this slice can ship dark (deploy, list, the rail, retry, and the
    # Brief routes all 404 by default) and be dogfooded by admins without
    # disturbing any already-launched surface above.
    app.include_router(v1_beats.router)

    # Mount frontend static files (only serves if dist/ exists)
    mount_frontend(app)

    return app


def _install_request_id(app: FastAPI) -> None:
    """Assign a request id used by the error envelope and echoed to the client.

    The envelope (``errors.error_response``) reads ``request.state.request_id``;
    this is the single place it is set. Full request-log emission is a separate
    observability concern (not part of the AL-020 auth surface).
    """

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


def _install_error_handlers(app: FastAPI) -> None:
    """Render every API error through the shared envelope (ported from habagou)."""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        message, details = _detail_parts(exc.detail)
        return error_response(
            request,
            status_code=exc.status_code,
            code=_http_error_code(exc.status_code),
            message=message,
            details=details,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ):
        # ``jsonable_encoder``, not ``exc.errors()`` raw (AL-410): a
        # ``model_validator`` that rejects its input with a plain
        # ``ValueError`` (``dtos/flashcards.py``'s ``UpdateCardRequest``,
        # the first DTO in this codebase to expose one to an HTTP body) makes
        # pydantic-core populate that error's ``ctx.error`` with the raw
        # exception *instance* — which ``JSONResponse``'s bare ``json.dumps``
        # cannot serialize, turning every such `422` into an unhandled `500`
        # instead. FastAPI's own default handler already guards this exact
        # case the same way (`fastapi.exception_handlers`); the human-readable
        # ``msg`` field survives either way, so nothing about the message a
        # client actually reads changes.
        return error_response(
            request,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="validation_error",
            message="request validation failed",
            details=jsonable_encoder(exc.errors()),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, _exc: Exception):
        return error_response(
            request,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="internal_error",
            message="internal server error",
        )


def _detail_parts(detail: object) -> tuple[str, object | None]:
    """Split an ``HTTPException`` detail into the envelope's message + details.

    A plain string detail is the message and carries no details — every Phase 1
    and Phase 2 route, unchanged.

    A **mapping** detail may name its own ``message``, and then it is used
    verbatim while the rest of the mapping becomes ``details``. Phase 2B's
    apply/undo conflicts need exactly that shape: the envelope's ``code`` is
    ``conflict`` for every ``409``, but the proposal card has to render *which*
    conflict ("this lesson has been started since") and offer the matching
    affordance, so the coded ``reason`` rides in ``details`` alongside a message
    written for a human. Without this, such a route would answer the useless
    "request failed" and hide its own sentence inside ``details``.
    """
    if isinstance(detail, str):
        return detail, None
    if isinstance(detail, Mapping):
        message = detail.get("message")
        if isinstance(message, str):
            rest = {key: value for key, value in detail.items() if key != "message"}
            return message, rest or None
    return "request failed", detail


def _http_error_code(status_code: int) -> str:
    return {
        401: "unauthenticated",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "validation_error",
        429: "rate_limited",
        502: "bad_gateway",
        503: "service_unavailable",
    }.get(status_code, f"http_{status_code}")


app = create_app()
