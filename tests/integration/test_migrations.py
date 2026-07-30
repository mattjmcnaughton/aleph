"""Migration integration tests: the tutor branch applies and reverses cleanly.

The per-test database is already at ``head`` (the conftest clones a template
migrated by Alembic), so these tests drive Alembic **down** to the Phase 1 head
and back up again, asserting the ``0003`` step is reversible and strictly
additive: Phase 1's tables and their data survive the round trip untouched
(Phase 2 TDD §4 — "Phase 1's tables are unchanged and un-migrated").

Synchronous by necessity: ``alembic/env.py`` calls ``asyncio.run``, so it cannot
be driven from inside a running event loop.
"""

from __future__ import annotations

import asyncio

from .conftest import connect, run_alembic

PHASE_1_HEAD = "0002_path_model_overrides"
PHASE_2_HEAD = "0003_tutor_conversations"

PHASE_1_TABLES = ("users", "paths", "units", "lessons", "quick_checks", "attempts")
PHASE_2_TABLES = ("conversations", "messages")


async def _tables(database_url: str) -> set[str]:
    connection = await connect(database_url)
    try:
        rows = await connection.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
    finally:
        await connection.close()
    return {row["tablename"] for row in rows}


async def _enum_types(database_url: str) -> set[str]:
    connection = await connect(database_url)
    try:
        rows = await connection.fetch(
            "SELECT typname FROM pg_type WHERE typtype = 'e'",
        )
    finally:
        await connection.close()
    return {row["typname"] for row in rows}


async def _columns(database_url: str, table: str) -> set[str]:
    connection = await connect(database_url)
    try:
        rows = await connection.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = $1",
            table,
        )
    finally:
        await connection.close()
    return {row["column_name"] for row in rows}


async def _seed_phase_1_row(database_url: str) -> str:
    """Insert a users row so the downgrade can be shown not to disturb it."""
    connection = await connect(database_url)
    try:
        return await connection.fetchval(
            "INSERT INTO users (id, issuer, subject, username, display_name) "
            "VALUES (gen_random_uuid(), 'iss', 'sub', 'migration-user', 'M') "
            "RETURNING username"
        )
    finally:
        await connection.close()


async def _count(database_url: str, table: str) -> int:
    connection = await connect(database_url)
    try:
        # ``table`` is always one of this module's constants, never test input.
        return await connection.fetchval(f"SELECT count(*) FROM {table}")
    finally:
        await connection.close()


def test_tutor_migration_downgrades_and_reapplies_cleanly(
    isolated_database: str,
) -> None:
    database_url = isolated_database

    at_head = asyncio.run(_tables(database_url))
    assert PHASE_2_TABLES[0] in at_head
    assert PHASE_2_TABLES[1] in at_head
    assert set(PHASE_1_TABLES) <= at_head

    username = asyncio.run(_seed_phase_1_row(database_url))

    run_alembic(database_url, PHASE_1_HEAD, downgrade=True)

    after_downgrade = asyncio.run(_tables(database_url))
    assert set(PHASE_2_TABLES) & after_downgrade == set()
    # Phase 1 is untouched by the reversal: tables *and* their rows survive.
    assert set(PHASE_1_TABLES) <= after_downgrade
    assert asyncio.run(_count(database_url, "users")) == 1
    # The tutor enum types are dropped with their tables (no orphan types left
    # behind to collide with a re-upgrade).
    assert {"message_role", "message_source"} & asyncio.run(
        _enum_types(database_url)
    ) == set()
    # Phase 1's enums are not collateral damage.
    assert {"level", "path_status", "lesson_generation_state"} <= asyncio.run(
        _enum_types(database_url)
    )

    run_alembic(database_url, PHASE_2_HEAD)

    reapplied = asyncio.run(_tables(database_url))
    assert set(PHASE_2_TABLES) <= reapplied
    assert set(PHASE_1_TABLES) <= reapplied
    assert asyncio.run(_count(database_url, "users")) == 1
    assert username == "migration-user"


def test_tutor_migration_creates_the_documented_columns(
    isolated_database: str,
) -> None:
    database_url = isolated_database

    assert asyncio.run(_columns(database_url, "conversations")) == {
        "id",
        "created_at",
        "updated_at",
        "path_id",
    }
    assert asyncio.run(_columns(database_url, "messages")) == {
        "id",
        "created_at",
        "updated_at",
        "conversation_id",
        "position",
        "role",
        "content",
        "lesson_id",
        "source",
        "tutor_check",
    }


def test_phase_1_tables_are_unchanged_by_the_tutor_migration(
    isolated_database: str,
) -> None:
    """The tutor branch adds tables; it alters none of Phase 1's."""
    database_url = isolated_database

    before = {
        table: asyncio.run(_columns(database_url, table)) for table in PHASE_1_TABLES
    }
    run_alembic(database_url, PHASE_1_HEAD, downgrade=True)
    after = {
        table: asyncio.run(_columns(database_url, table)) for table in PHASE_1_TABLES
    }

    assert before == after
