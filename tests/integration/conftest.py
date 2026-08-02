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
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url

from alembic import command
from aleph import db, events
from aleph.app import create_app
from aleph.config import settings
from aleph.dependencies import get_current_user
from aleph.logging import configure_logging
from aleph.models import User

TEMPLATE_PREFIX = "aleph_test_base"
RUN_ID = uuid.uuid4().hex[:12]

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Generator
    from typing import Any

    from pydantic_ai.models import Model
    from sqlalchemy.ext.asyncio import AsyncSession


def pytest_configure() -> None:
    """Install compact, credential-safe logging in every pytest-xdist worker."""
    configure_logging()


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
def isolated_database(base_database_url: URL) -> Generator[str]:
    """Clone a fresh database for this test; yields its URL.

    Autouse, so most tests never name it. Tests that drive Alembic directly
    (the migration up/down test) request it by name to learn which database to
    point the migration runner at.
    """
    database_name = f"aleph_test_{uuid.uuid4().hex}"
    test_url = _with_database(base_database_url, database_name)
    template_name = _database_name(base_database_url)
    created = False
    try:
        asyncio.run(_create_database_from_template(test_url, template_name))
        created = True
        asyncio.run(db.configure_database_url(_render_url(test_url)))

        yield _render_url(test_url)
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
        run_alembic(_render_url(template_url), "head")
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


async def connect(database_url: URL | str) -> asyncpg.Connection:
    """Open a raw asyncpg connection to ``database_url``.

    Unpacks a SQLAlchemy URL into asyncpg's keywords; a ``host`` query parameter
    wins over the host component, so a socket-directory URL
    (``...@/db?host=/var/run/postgresql``) connects the way it reads.
    """
    url = database_url if isinstance(database_url, URL) else make_url(database_url)
    return await asyncpg.connect(
        user=url.username,
        password=url.password,
        database=url.database,
        host=url.query.get("host") or url.host,
        port=url.port,
    )


async def _connect_admin(database_url: URL) -> asyncpg.Connection:
    return await connect(_with_database(database_url, "postgres"))


def run_alembic(database_url: str, revision: str, *, downgrade: bool = False) -> None:
    """Drive Alembic against ``database_url``, up or down to ``revision``.

    ``alembic/env.py`` reads the URL from :mod:`aleph.config` at run time, so the
    setting (and ``DATABASE_URL``, which a fresh ``Settings`` read would pick up)
    is swapped for the duration and restored afterwards. Synchronous: ``env.py``
    calls ``asyncio.run``, so this must never be invoked from a running loop.
    """
    previous_env_url = os.environ.get("DATABASE_URL")
    previous_settings_url = settings.database_url
    os.environ["DATABASE_URL"] = database_url
    settings.database_url = database_url
    try:
        config = Config("alembic.ini")
        if downgrade:
            command.downgrade(config, revision)
        else:
            command.upgrade(config, revision)
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

    **Recording is not sequencing.** By default a spawned task is scheduled the
    moment it is handed over, exactly as in production — so it runs at the test's
    next ``await``, whether or not the test has reached its ``drain()`` yet. That
    is right for a suite whose subject is the background work itself (poll until
    ready, reconcile a stale row): the task should make progress on its own.

    It is wrong for a suite that wants to observe what a *request* wrote before
    the follow-up work it triggered can touch it. ``hold=True`` is for those:
    every spawned task parks until :meth:`drain` opens the gate, which turns
    "immediately after the response" into a moment that actually exists.
    """

    def __init__(self, *, hold: bool = False) -> None:
        self.tasks: list[asyncio.Task[Any]] = []
        self._hold = hold
        self._gate = asyncio.Event()
        # Held coroutines that have not started running yet. Tracked so teardown
        # can close them: a coroutine that is cancelled before its wrapper is
        # ever scheduled has nowhere to clean itself up, and the garbage
        # collector complains (``coroutine ... was never awaited``) about work
        # the test deliberately declined to run.
        self._unstarted: set[Coroutine[Any, Any, Any]] = set()

    def __call__(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        if self._hold:
            self._unstarted.add(coro)
            task = asyncio.create_task(self._parked(coro))
        else:
            task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task

    async def _parked(self, coro: Coroutine[Any, Any, Any]) -> Any:
        """Hold ``coro`` at the gate, then run it whole once ``drain`` opens it."""
        try:
            await self._gate.wait()
        except BaseException:
            # Cancelled at the gate: this frame owns the coroutine, so it is the
            # one that has to close it.
            self._unstarted.discard(coro)
            coro.close()
            raise
        self._unstarted.discard(coro)
        return await coro

    async def drain(self) -> None:
        """Await every spawned task (including ones spawned while draining)."""
        self._gate.set()
        while self.tasks:
            batch = self.tasks
            self.tasks = []
            await asyncio.gather(*batch)
        # Re-arm: work spawned *after* this drain is parked like the work before
        # it, so a test that drains twice observes the same thing both times.
        self._gate.clear()

    async def cancel_pending(self) -> None:
        """Drop whatever the test never drained. Teardown for ``hold`` suites.

        A parked task waits on a gate nobody will open again, so letting the loop
        close over it prints ``Task was destroyed but it is pending``. Cancelling
        is the honest end: the test said nothing about that work, so it does not
        happen.
        """
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []
        # Whatever is still here was cancelled before its wrapper ever ran, so
        # no frame got the chance to close it.
        for coro in self._unstarted:
            coro.close()
        self._unstarted.clear()


def stub_resolver() -> Callable[[str], Model]:
    """A ``resolve_model_fn`` returning the real deterministic stub (sentinels)."""
    from aleph.services.stub_model import build_stub_model

    model = build_stub_model()
    return lambda _model_id: model


def recording_resolver() -> tuple[Callable[[str], Model], list[str]]:
    """A ``resolve_model_fn`` that records every model id it is asked to resolve.

    Returns the resolver plus the shared list it appends to, so a test can assert
    which OpenRouter ids the orchestrator actually routed to — the proof that an
    admin picker override (§5.3) reaches the outline/lesson model calls (and that
    it survives the DB-driven trigger/poll boundary, since the override travels on
    the persisted path row, not the request). The underlying model is the
    deterministic stub, so content stays schema-valid.
    """
    from aleph.services.stub_model import build_stub_model

    model = build_stub_model()
    calls: list[str] = []

    def resolve(model_id: str) -> Model:
        calls.append(model_id)
        return model

    return resolve, calls


async def wait_until_lock_waiters(expected: int) -> None:
    """Block (yielding, no timed sleep) until ``expected`` backends on *this* test
    database are waiting on a lock.

    Deterministic synchronization for contention tests: instead of sleeping and
    hoping the competitor has reached the lock, poll ``pg_stat_activity`` until
    it provably has. Covers both row locks and the transaction-id wait a
    conflicting unique-index insert performs. Scoped to ``current_database()`` so
    parallel xdist workers (each its own cloned DB) never cross-count.
    """
    while True:
        async with db.async_session() as monitor:
            result = await monitor.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE wait_event_type = 'Lock' "
                    "AND datname = current_database()"
                )
            )
            if (result.scalar() or 0) >= expected:
                return
        await asyncio.sleep(0)  # yield the loop; not a timed wait


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


@pytest.fixture
def tutor_flag_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the ``tutor`` flag on globally for one test (AL-203).

    Phase 2 ships dark: ``tutor`` resolves off for a plain learner, so an
    integration test that drives the tutor surface as one would otherwise be
    testing the flag gate rather than the tutor. Requesting this fixture flips
    the *global default* — the same lever AL-270 pulls at launch and
    ``scripts/e2e_backend.py`` sets for the browser suite — rather than making
    the test's learner an admin or seeding an override row, so the surface is
    exercised exactly as a post-launch learner meets it.

    Shared here so every ticket that adds tutor coverage (AL-221, AL-220,
    AL-240) reaches for one definition instead of respelling the settings
    mutation, which is how "the fixture that forgot the flag" becomes a
    mysterious 404.
    """
    _enable_flag_globally(monkeypatch, "tutor")


@pytest.fixture
def shaping_flag_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the ``shaping`` flag on globally for one test (AL-301).

    The Phase 2B twin of ``tutor_flag_enabled``, for the same reason: Phase 2B
    ships dark, so a test that drives the shaping rail, its stream or its
    apply/undo endpoints as a plain learner would otherwise be testing the flag
    gate. Flipping the *global default* is the same lever AL-370 pulls at launch
    and ``scripts/e2e_backend.py`` sets for the browser suite.
    """
    _enable_flag_globally(monkeypatch, "shaping")


@pytest.fixture
def tutor_flag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the ``tutor`` flag off globally for one test (AL-270).

    The mirror of ``tutor_flag_enabled``, and now the one that does the work:
    since AL-270 launched the tutor its code default is ``True``, so a test that
    wants to prove the ``404`` gate has to *close* it rather than assume it.

    Uses the settings map — the documented kill switch — rather than patching
    ``FLAG_DEFAULTS``, so what the test exercises is the lever an operator would
    actually pull. Note the asymmetry that makes it a real kill switch: an
    explicit ``:off`` outranks ``ADMIN_DEFAULT_FLAGS``, so this closes the
    surface for admins too.
    """
    _disable_flag_globally(monkeypatch, "tutor")


@pytest.fixture
def shaping_flag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the ``shaping`` flag off globally for one test (AL-370).

    The Phase 2B twin of ``tutor_flag_disabled``, for the same reason.
    """
    _disable_flag_globally(monkeypatch, "shaping")


@pytest.fixture
def streaks_flag_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the ``streaks`` flag on globally for one test (Phase 5 TDD D7).

    The Phase 5 twin of ``tutor_flag_enabled``/``shaping_flag_enabled``, and
    redundant for the same reason they are: since the launch flip the code
    default is ``True``, so the Progress surface is open without it. Kept, and
    still requested by the tests that drive that surface, because it states
    which flag a test's subject hangs off — and because it is what those tests
    would need again if this flag ever went dark.
    """
    _enable_flag_globally(monkeypatch, "streaks")


@pytest.fixture
def streaks_flag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the ``streaks`` flag off globally for one test.

    The mirror of ``streaks_flag_enabled``, and now the one that does the work:
    since the launch flip the code default is ``True``, so a test that wants to
    prove the ``404`` gate has to *close* the flag rather than assume it — the
    same move ``tutor_flag_disabled``/``shaping_flag_disabled`` make, through
    the same documented kill switch.
    """
    _disable_flag_globally(monkeypatch, "streaks")


def _enable_flag_globally(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    """Add ``key:on`` to ``FEATURE_FLAG_DEFAULTS``, keeping the entries already set."""
    _set_flag_globally(monkeypatch, key, state="on")


def _disable_flag_globally(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    """Add ``key:off`` to ``FEATURE_FLAG_DEFAULTS``, keeping the entries already set."""
    _set_flag_globally(monkeypatch, key, state="off")


def _set_flag_globally(
    monkeypatch: pytest.MonkeyPatch, key: str, *, state: str
) -> None:
    """Write one ``key:state`` entry into ``FEATURE_FLAG_DEFAULTS``.

    Additive rather than assigning the whole string, so a test that requests two
    flag fixtures gets both — the obvious ``setattr(..., "shaping:on")`` would
    silently clobber the tutor entry depending on fixture order. Any existing
    entry for ``key`` is dropped first, so on-then-off (or the reverse) across
    two fixtures resolves to the last one applied rather than to whichever the
    parser happened to see first.
    """
    entries = [
        entry.strip()
        for entry in settings.feature_flag_defaults.split(",")
        if entry.strip() and not entry.strip().startswith(f"{key}:")
    ]
    entries.append(f"{key}:{state}")
    monkeypatch.setattr(settings, "feature_flag_defaults", ",".join(entries))


# --------------------------------------------------------------------------- #
# Captured product events (AL-070 / AL-240)
#
# ``capfire`` (logfire's pytest11 plugin) exposes an in-memory exporter; the
# StructlogProcessor ``configure_logging`` installs lands each ``events.py``
# emission there as a *log* record whose ``name`` is the event name and whose
# ``attributes`` are its fields. Shared by every suite that asserts a real route
# emitted a real event.
# --------------------------------------------------------------------------- #


def captured_records(capfire: Any, name: str) -> list[dict[str, Any]]:
    """Every captured log record for the product event ``name``, in order."""
    return [
        span
        for span in capfire.exporter.exported_spans_as_dict()
        if span["attributes"].get("logfire.span_type") == "log" and span["name"] == name
    ]


def assert_event(capfire: Any, name: str) -> dict[str, Any]:
    """The first captured ``name`` record's attributes, manifest fields checked.

    Asserting against ``EVENT_FIELDS`` here (rather than a hand-listed set) is
    what makes this tier prove the *manifest* the metric queries are checked
    against is what a real route really emits.
    """
    records = captured_records(capfire, name)
    assert records, f"no {name} event captured"
    attributes = records[0]["attributes"]
    missing = events.EVENT_FIELDS[name] - set(attributes)
    assert not missing, f"{name} missing fields {sorted(missing)}"
    return attributes
