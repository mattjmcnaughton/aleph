"""Migration integration tests: each branch applies and reverses cleanly.

The per-test database is already at ``head`` (the conftest clones a template
migrated by Alembic), so these tests drive Alembic **down** to an earlier head
and back up again, asserting each step is reversible and no more invasive than
its TDD says.

**A step is asserted from its own vantage point, not from ``head``.** A test
about the ``0003`` step first drives the database down to ``0003``, and only
then compares — otherwise every later migration (``0004``, ``0005``, …) would
turn "the tutor branch alters none of Phase 1's tables" into an assertion about
the *whole* stack, and each new phase would break tests about the previous one.

Synchronous by necessity: ``alembic/env.py`` calls ``asyncio.run``, so it cannot
be driven from inside a running event loop.
"""

from __future__ import annotations

import asyncio

from .conftest import connect, run_alembic

PHASE_1_HEAD = "0002_path_model_overrides"
PHASE_2_HEAD = "0003_tutor_conversations"
# AL-203's step, stacked on the tutor branch: the flag-override table.
FLAGS_HEAD = "0004_user_feature_overrides"
# AL-300's step: the Phase 2B shaping branch.
SHAPING_HEAD = "0005_shaping"

PHASE_1_TABLES = ("users", "paths", "units", "lessons", "quick_checks", "attempts")
PHASE_2_TABLES = ("conversations", "messages")
FLAGS_TABLE = "user_feature_overrides"
CHANGES_TABLE = "path_changes"


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
    # Asserted at ``0003``: later branches add their own columns to these
    # tables (2B's ``kind``/``proposal``), which is their business, not this
    # step's.
    run_alembic(database_url, PHASE_2_HEAD, downgrade=True)

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
    # The comparison brackets *this* step: down to ``0003`` first, so what is
    # measured either side is the ``0003`` transition alone.
    run_alembic(database_url, PHASE_2_HEAD, downgrade=True)

    before = {
        table: asyncio.run(_columns(database_url, table)) for table in PHASE_1_TABLES
    }
    run_alembic(database_url, PHASE_1_HEAD, downgrade=True)
    after = {
        table: asyncio.run(_columns(database_url, table)) for table in PHASE_1_TABLES
    }

    assert before == after


# --------------------------------------------------------------------------- #
# AL-203: the feature-flag override table (migration 0004)
# --------------------------------------------------------------------------- #


async def _seed_override(database_url: str, flag_key: str) -> None:
    """Attach an override to the seeded users row (needs the FK to resolve)."""
    connection = await connect(database_url)
    try:
        await connection.execute(
            "INSERT INTO user_feature_overrides (user_id, flag_key, enabled) "
            "SELECT id, $1, TRUE FROM users LIMIT 1",
            flag_key,
        )
    finally:
        await connection.close()


def test_feature_flag_migration_downgrades_and_reapplies_cleanly(
    isolated_database: str,
) -> None:
    database_url = isolated_database

    assert FLAGS_TABLE in asyncio.run(_tables(database_url))
    asyncio.run(_seed_phase_1_row(database_url))
    asyncio.run(_seed_override(database_url, "tutor"))
    assert asyncio.run(_count(database_url, FLAGS_TABLE)) == 1

    run_alembic(database_url, PHASE_2_HEAD, downgrade=True)

    after_downgrade = asyncio.run(_tables(database_url))
    assert FLAGS_TABLE not in after_downgrade
    # Strictly additive: the tutor branch and Phase 1 survive the reversal, rows
    # included (the override rows go with their own table and nothing else).
    assert set(PHASE_2_TABLES) <= after_downgrade
    assert set(PHASE_1_TABLES) <= after_downgrade
    assert asyncio.run(_count(database_url, "users")) == 1

    run_alembic(database_url, FLAGS_HEAD)

    assert FLAGS_TABLE in asyncio.run(_tables(database_url))
    assert asyncio.run(_count(database_url, "users")) == 1
    # The table comes back empty — overrides are exceptions, never data to
    # migrate (a deleted flag needs no data migration for the same reason).
    assert asyncio.run(_count(database_url, FLAGS_TABLE)) == 0


def test_feature_flag_migration_creates_the_documented_columns(
    isolated_database: str,
) -> None:
    assert asyncio.run(_columns(isolated_database, FLAGS_TABLE)) == {
        "user_id",
        "flag_key",
        "enabled",
        "updated_at",
    }


def test_feature_flag_overrides_cascade_when_a_user_is_deleted(
    isolated_database: str,
) -> None:
    """``ON DELETE CASCADE`` is a schema property, asserted at the SQL level."""
    database_url = isolated_database
    asyncio.run(_seed_phase_1_row(database_url))
    asyncio.run(_seed_override(database_url, "tutor"))

    async def _delete_users() -> None:
        connection = await connect(database_url)
        try:
            await connection.execute("DELETE FROM users")
        finally:
            await connection.close()

    asyncio.run(_delete_users())

    assert asyncio.run(_count(database_url, FLAGS_TABLE)) == 0


def test_earlier_tables_are_unchanged_by_the_feature_flag_migration(
    isolated_database: str,
) -> None:
    """The flag branch adds a table; it alters none of the existing ones."""
    database_url = isolated_database
    tracked = (*PHASE_1_TABLES, *PHASE_2_TABLES)
    # Bracket the ``0004`` transition only (see the module docstring).
    run_alembic(database_url, FLAGS_HEAD, downgrade=True)

    before = {table: asyncio.run(_columns(database_url, table)) for table in tracked}
    run_alembic(database_url, PHASE_2_HEAD, downgrade=True)
    after = {table: asyncio.run(_columns(database_url, table)) for table in tracked}

    assert before == after


# --------------------------------------------------------------------------- #
# AL-300: the shaping branch (migration 0005)
# --------------------------------------------------------------------------- #


async def _seed_lesson_conversation(database_url: str) -> None:
    """A 2A-shaped thread: user -> path -> unit -> lesson -> conversation.

    Seeded at ``0004`` — *before* ``conversations.kind`` exists — so the
    backfill assertion is about a genuinely pre-2B row rather than one this test
    wrote through the new column.
    """
    connection = await connect(database_url)
    try:
        await connection.execute(
            """
            WITH u AS (
                INSERT INTO users (id, issuer, subject, username, display_name)
                VALUES (gen_random_uuid(), 'iss', 'sub-2b', 'shaping-user', 'S')
                RETURNING id
            ), p AS (
                INSERT INTO paths (id, user_id, topic, level, status)
                SELECT gen_random_uuid(), u.id, 'Rust ownership',
                       'some_experience', 'ready'
                FROM u
                RETURNING id
            )
            INSERT INTO conversations (id, path_id)
            SELECT gen_random_uuid(), p.id FROM p
            """
        )
    finally:
        await connection.close()


async def _conversation_kinds(database_url: str) -> list[str]:
    connection = await connect(database_url)
    try:
        rows = await connection.fetch("SELECT kind::text AS kind FROM conversations")
    finally:
        await connection.close()
    return [row["kind"] for row in rows]


async def _unique_constraint_columns(database_url: str, table: str) -> dict[str, str]:
    """``{constraint name: its column list}`` for ``table``'s UNIQUE constraints."""
    connection = await connect(database_url)
    try:
        rows = await connection.fetch(
            """
            SELECT conname, pg_get_constraintdef(oid) AS definition
            FROM pg_constraint
            WHERE conrelid = $1::regclass AND contype = 'u'
            """,
            table,
        )
    finally:
        await connection.close()
    return {row["conname"]: row["definition"] for row in rows}


def test_shaping_migration_downgrades_and_reapplies_cleanly(
    isolated_database: str,
) -> None:
    database_url = isolated_database

    at_head = asyncio.run(_tables(database_url))
    assert CHANGES_TABLE in at_head

    asyncio.run(_seed_phase_1_row(database_url))

    run_alembic(database_url, FLAGS_HEAD, downgrade=True)

    after_downgrade = asyncio.run(_tables(database_url))
    assert CHANGES_TABLE not in after_downgrade
    # Everything before 2B survives the reversal, rows included.
    assert set(PHASE_1_TABLES) <= after_downgrade
    assert set(PHASE_2_TABLES) <= after_downgrade
    assert FLAGS_TABLE in after_downgrade
    assert asyncio.run(_count(database_url, "users")) == 1
    # The 2B enum types go with the step (no orphan types to collide with a
    # re-upgrade).
    assert {
        "conversation_kind",
        "path_change_kind",
        "path_change_status",
    } & asyncio.run(_enum_types(database_url)) == set()
    # Earlier phases' enums are not collateral damage.
    assert {"message_role", "message_source", "level", "path_status"} <= asyncio.run(
        _enum_types(database_url)
    )

    run_alembic(database_url, SHAPING_HEAD)

    reapplied = asyncio.run(_tables(database_url))
    assert CHANGES_TABLE in reapplied
    assert set(PHASE_1_TABLES) <= reapplied
    assert asyncio.run(_count(database_url, "users")) == 1
    assert asyncio.run(_count(database_url, CHANGES_TABLE)) == 0


def test_shaping_migration_creates_the_documented_columns(
    isolated_database: str,
) -> None:
    database_url = isolated_database

    assert asyncio.run(_columns(database_url, CHANGES_TABLE)) == {
        "id",
        "created_at",
        "updated_at",
        "path_id",
        "message_id",
        "kind",
        "payload",
        "status",
        "applied_at",
        "undone_at",
    }
    # The three additive columns on existing tables (TDD §4).
    assert "kind" in asyncio.run(_columns(database_url, "conversations"))
    assert "proposal" in asyncio.run(_columns(database_url, "messages"))
    assert "revision_instruction" in asyncio.run(_columns(database_url, "lessons"))


def test_existing_conversations_are_backfilled_to_the_lesson_kind(
    isolated_database: str,
) -> None:
    """The 2A thread a learner already has becomes a ``lesson`` thread (D3).

    The row is written at ``0004``, before the column exists, so this proves the
    *migration* backfills it — the ``DEFAULT 'lesson'`` on the ``ALTER``, which
    is also why the widened unique constraint can be swapped in the same
    transaction without finding a NULL.
    """
    database_url = isolated_database

    run_alembic(database_url, FLAGS_HEAD, downgrade=True)
    asyncio.run(_seed_lesson_conversation(database_url))

    run_alembic(database_url, SHAPING_HEAD)

    assert asyncio.run(_conversation_kinds(database_url)) == ["lesson"]


def test_shaping_migration_swaps_the_conversation_uniqueness(
    isolated_database: str,
) -> None:
    """``UNIQUE (path_id)`` becomes ``UNIQUE (path_id, kind)`` — swapped, not added."""
    database_url = isolated_database

    at_head = asyncio.run(_unique_constraint_columns(database_url, "conversations"))
    assert "uq_conversations_path" not in at_head
    assert "(path_id, kind)" in at_head["uq_conversations_path_kind"]

    run_alembic(database_url, FLAGS_HEAD, downgrade=True)

    reversed_ = asyncio.run(_unique_constraint_columns(database_url, "conversations"))
    assert "uq_conversations_path_kind" not in reversed_
    assert "(path_id)" in reversed_["uq_conversations_path"]


def test_earlier_tables_keep_their_columns_through_the_shaping_reversal(
    isolated_database: str,
) -> None:
    """2B adds columns and one table; it removes nothing from what came before."""
    database_url = isolated_database
    tracked = (*PHASE_1_TABLES, *PHASE_2_TABLES, FLAGS_TABLE)

    before = {table: asyncio.run(_columns(database_url, table)) for table in tracked}
    run_alembic(database_url, FLAGS_HEAD, downgrade=True)
    after = {table: asyncio.run(_columns(database_url, table)) for table in tracked}

    added = {
        "conversations": {"kind"},
        "messages": {"proposal"},
        "lessons": {"revision_instruction"},
    }
    assert {
        table: columns - added.get(table, set()) for table, columns in before.items()
    } == after
