"""Per-test Postgres database fixtures (template-database clone pattern).

Adapted from habagou's integration conftest, trimmed of its corpus/seed/auth
specifics. Parallel-safe: a template database is created once per pytest-xdist
worker (its name carries a per-process ``RUN_ID`` and the ``worker_id``), and
every test clones a fresh database from it via ``CREATE DATABASE ... TEMPLATE``,
so tests never share state and can run concurrently.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import TYPE_CHECKING, Annotated

import asyncpg
import pytest
from alembic.config import Config
from fastapi import Depends, FastAPI
from sqlalchemy.engine import URL, make_url

from alembic import command
from aleph import db
from aleph.app import create_app
from aleph.config import settings
from aleph.dependencies import get_current_user
from aleph.models import User

TEMPLATE_PREFIX = "aleph_test_base"
RUN_ID = uuid.uuid4().hex[:12]

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Generator
    from typing import Any

    from pydantic_ai.models import Model
    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def worker_id() -> str:
    return os.environ.get("PYTEST_XDIST_WORKER", "gw0")


@pytest.fixture(scope="session")
def base_database_url(worker_id: str) -> URL:
    url = make_url(settings.database_url)
    return _with_database(url, f"{TEMPLATE_PREFIX}_{RUN_ID}_{worker_id}")


@pytest.fixture(scope="session", autouse=True)
def template_database(base_database_url: URL) -> Generator[None]:
    _create_template_database(base_database_url)
    try:
        yield
    finally:
        asyncio.run(db.dispose_engine())
        asyncio.run(_drop_database(base_database_url))


@pytest.fixture(autouse=True)
def isolated_database(base_database_url: URL) -> Generator[None]:
    database_name = f"aleph_test_{uuid.uuid4().hex}"
    test_url = _with_database(base_database_url, database_name)
    template_name = _database_name(base_database_url)
    created = False
    try:
        asyncio.run(_create_database_from_template(test_url, template_name))
        created = True
        asyncio.run(db.configure_database_url(_render_url(test_url)))

        yield
    finally:
        asyncio.run(db.dispose_engine())
        if created:
            asyncio.run(_drop_database(test_url))


def _create_template_database(template_url: URL) -> None:
    asyncio.run(_drop_database(template_url))
    created = False
    try:
        asyncio.run(_create_database(template_url))
        created = True
        _run_migrations(_render_url(template_url))
    except Exception:
        asyncio.run(db.dispose_engine())
        if created:
            asyncio.run(_drop_database(template_url))
        raise
    else:
        asyncio.run(db.dispose_engine())


async def _create_database(database_url: URL) -> None:
    connection = await _connect_admin(database_url)
    try:
        await connection.execute(
            f'CREATE DATABASE "{database_url.database}" TEMPLATE template0'
        )
    finally:
        await connection.close()


async def _create_database_from_template(database_url: URL, template_name: str) -> None:
    connection = await _connect_admin(database_url)
    try:
        await connection.execute(
            f'CREATE DATABASE "{database_url.database}" TEMPLATE "{template_name}"'
        )
    finally:
        await connection.close()


async def _drop_database(database_url: URL) -> None:
    if database_url.database is None:
        raise RuntimeError("database URL must include a database name")

    connection = await _connect_admin(database_url)
    try:
        await connection.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = $1 AND pid <> pg_backend_pid()
            """,
            database_url.database,
        )
        await connection.execute(f'DROP DATABASE IF EXISTS "{database_url.database}"')
    finally:
        await connection.close()


async def _connect_admin(database_url: URL) -> asyncpg.Connection:
    admin_url = _with_database(database_url, "postgres")
    return await asyncpg.connect(
        user=admin_url.username,
        password=admin_url.password,
        database=admin_url.database,
        host=admin_url.query.get("host") or admin_url.host,
        port=admin_url.port,
    )


def _run_migrations(database_url: str) -> None:
    previous_env_url = os.environ.get("DATABASE_URL")
    previous_settings_url = settings.database_url
    os.environ["DATABASE_URL"] = database_url
    settings.database_url = database_url
    try:
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        settings.database_url = previous_settings_url
        if previous_env_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_env_url


def _with_database(url: URL, database: str) -> URL:
    return url.set(database=database)


def _database_name(url: URL) -> str:
    if url.database is None:
        raise RuntimeError("database URL must include a database name")
    return url.database


def _render_url(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def build_authprobe_app() -> FastAPI:
    """A fresh app plus the smallest possible consumer of ``get_current_user``.

    ``/api/v1/_authprobe`` exists only for the auth tests: it lets the ``401``
    gate and a successful cookie be asserted before any real ``/api/v1/*`` route
    (AL-050/051) exists. Shared here so the stubbed-client and the real-Keycloak
    integration tests use one definition.
    """
    app = create_app()

    @app.get("/api/v1/_authprobe")
    async def _authprobe(
        user: Annotated[User, Depends(get_current_user)],
    ) -> dict[str, str]:
        return {"id": str(user.id)}

    return app


# --------------------------------------------------------------------------- #
# Shared generation test doubles
#
# ``CollectingSpawn`` and ``stub_resolver`` are the drainable spawn seam and the
# deterministic-stub model resolver used by every integration test that drives
# the generation orchestrator (``test_generation`` and the ``test_paths_api``
# HTTP surface). They live here in the shared conftest rather than in one test
# module so neither has to reach across into the other's namespace.
# --------------------------------------------------------------------------- #


class CollectingSpawn:
    """A ``spawn`` seam that records tasks so a test can await them.

    Production passes ``asyncio.create_task`` (AL-041 wraps it with a registry +
    semaphore); tests need to await the fire-and-forget work deterministically.
    """

    def __init__(self) -> None:
        self.tasks: list[asyncio.Task[Any]] = []

    def __call__(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task

    async def drain(self) -> None:
        """Await every spawned task (including ones spawned while draining)."""
        while self.tasks:
            batch = self.tasks
            self.tasks = []
            await asyncio.gather(*batch)


def stub_resolver() -> Callable[[str], Model]:
    """A ``resolve_model_fn`` returning the real deterministic stub (sentinels)."""
    from aleph.services.stub_model import build_stub_model

    model = build_stub_model()
    return lambda _model_id: model


async def create_user(
    session: AsyncSession,
    *,
    username: str = "test-user",
    display_name: str = "Test User",
    email: str | None = "test@example.com",
    issuer: str = "https://issuer.example.test",
    subject: str | None = None,
) -> User:
    """Insert and flush a learner account for arrange steps."""
    user = User(
        issuer=issuer,
        subject=subject or uuid.uuid4().hex,
        username=username,
        display_name=display_name,
        email=email,
    )
    session.add(user)
    await session.flush()
    return user
