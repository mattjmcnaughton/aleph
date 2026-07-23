"""Integration-suite scaffold smoke.

Placeholder proving the integration suite collects and runs green. It asserts
the async database engine is wired without opening a connection. AL-003 replaces
this with real Postgres-backed integration tests (and the CI Postgres service).
"""

from __future__ import annotations

from aleph.db import engine


def test_integration_scaffold_is_wired() -> None:
    assert engine.url.get_backend_name() == "postgresql"
    assert engine.url.get_driver_name() == "asyncpg"
