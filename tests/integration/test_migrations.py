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

import asyncpg
import pytest

from .conftest import connect, run_alembic

PHASE_1_HEAD = "0002_path_model_overrides"
PHASE_2_HEAD = "0003_tutor_conversations"
# AL-203's step, stacked on the tutor branch: the flag-override table.
FLAGS_HEAD = "0004_user_feature_overrides"
# AL-300's step: the Phase 2B shaping branch.
SHAPING_HEAD = "0005_shaping"
# AL-320's step: shaping messages are path-level, so ``messages.lesson_id``
# becomes nullable.
MESSAGE_LESSON_HEAD = "0006_shaping_message_lesson"
# AL-321's step: one **applied** Change per proposal message, in the database.
APPLIED_CHANGE_HEAD = "0007_applied_change_uniqueness"
APPLIED_CHANGE_INDEX = "uq_path_changes_applied_message"
# Path title + Guidance's step: two additive, nullable columns on ``paths``.
TITLE_GUIDANCE_HEAD = "0008_path_title_and_guidance"
# Phase 5 streaks' step (D6): one partial index covering the one query.
STREAK_INDEX_HEAD = "0009_lesson_completed_at_index"
STREAK_INDEX_NAME = "ix_lessons_path_id_completed_at"

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

    # Every column added *after* ``FLAGS_HEAD`` — the downgrade above unwinds the
    # whole span, not just 2B, so this map grows with each later migration even
    # though the test is named for the shaping reversal. ``paths.title`` /
    # ``paths.guidance`` arrive in 0008; leaving them out would read as "the
    # downgrade dropped a column it should have kept", which is exactly the
    # regression this test exists to catch.
    added = {
        "conversations": {"kind"},
        "messages": {"proposal"},
        "lessons": {"revision_instruction"},
        "paths": {"title", "guidance"},
    }
    assert {
        table: columns - added.get(table, set()) for table, columns in before.items()
    } == after


# --------------------------------------------------------------------------- #
# AL-320: shaping messages are path-level (migration 0006)
#
# The one schema gap TDD §4 did not spell out. ``messages.lesson_id`` is 2A's
# ``NOT NULL`` — right for a turn asked *in* a lesson, impossible for a shaping
# turn, which is about the path as a whole. The step is a single dropped
# ``NOT NULL``; what is worth asserting is that it really is only that, and that
# the reversal restores the constraint cleanly by removing the rows that could
# not have existed before it.
# --------------------------------------------------------------------------- #


async def _is_nullable(database_url: str, table: str, column: str) -> bool:
    connection = await connect(database_url)
    try:
        value = await connection.fetchval(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2",
            table,
            column,
        )
    finally:
        await connection.close()
    return value == "YES"


async def _seed_both_threads(database_url: str) -> None:
    """A path with an in-lesson turn *and* a shaping turn (the 0006 shape).

    The lesson message names its lesson; the shaping message names none, which
    is only expressible at ``0006`` — and is exactly the row the downgrade has
    to clear before ``NOT NULL`` can come back.
    """
    connection = await connect(database_url)
    try:
        await connection.execute(
            """
            WITH u AS (
                INSERT INTO users (id, issuer, subject, username, display_name)
                VALUES (gen_random_uuid(), 'iss', 'sub-320', 'shaping-msg', 'S')
                RETURNING id
            ), p AS (
                INSERT INTO paths (id, user_id, topic, level, status)
                SELECT gen_random_uuid(), u.id, 'Rust ownership',
                       'some_experience', 'ready'
                FROM u
                RETURNING id
            ), un AS (
                INSERT INTO units (id, path_id, position, title, summary)
                SELECT gen_random_uuid(), p.id, 1, 'Foundations', 's' FROM p
                RETURNING id, path_id
            ), l AS (
                INSERT INTO lessons (
                    id, path_id, unit_id, position_in_path, position_in_unit,
                    title, generation_state
                )
                SELECT gen_random_uuid(), un.path_id, un.id, 1, 1,
                       'What ownership is', 'generated'
                FROM un
                RETURNING id, path_id
            ), lc AS (
                INSERT INTO conversations (id, path_id, kind)
                SELECT gen_random_uuid(), l.path_id, 'lesson' FROM l
                RETURNING id
            ), sc AS (
                INSERT INTO conversations (id, path_id, kind)
                SELECT gen_random_uuid(), l.path_id, 'shaping' FROM l
                RETURNING id
            ), lm AS (
                INSERT INTO messages (
                    id, conversation_id, lesson_id, position, role, content
                )
                SELECT gen_random_uuid(), lc.id, l.id, 1, 'learner', 'in a lesson'
                FROM lc, l
                RETURNING id
            )
            INSERT INTO messages (
                id, conversation_id, lesson_id, position, role, content
            )
            SELECT gen_random_uuid(), sc.id, NULL, 1, 'learner', 'about the path'
            FROM sc
            """
        )
    finally:
        await connection.close()


async def _message_contents(database_url: str) -> set[str]:
    connection = await connect(database_url)
    try:
        rows = await connection.fetch("SELECT content FROM messages")
    finally:
        await connection.close()
    return {row["content"] for row in rows}


def test_the_message_lesson_step_only_drops_a_not_null(
    isolated_database: str,
) -> None:
    """At ``head`` the column is nullable; at ``0005`` it is not. Nothing else moves."""
    database_url = isolated_database

    assert asyncio.run(_is_nullable(database_url, "messages", "lesson_id"))
    at_head = asyncio.run(_columns(database_url, "messages"))

    run_alembic(database_url, SHAPING_HEAD, downgrade=True)

    assert not asyncio.run(_is_nullable(database_url, "messages", "lesson_id"))
    assert asyncio.run(_columns(database_url, "messages")) == at_head, (
        "the step adds and removes no column — it is one constraint"
    )


def test_the_message_lesson_step_reverses_by_dropping_shaping_messages(
    isolated_database: str,
) -> None:
    """The reversal clears exactly the rows ``NOT NULL`` cannot hold.

    A shaping message is *defined* by having no lesson, so restoring the
    constraint means dropping those rows — data-loss-on-downgrade, the standard
    posture, and the same one ``0005`` takes for shaping conversations. The
    in-lesson turn is untouched, which is the half that matters: a downgrade
    must not cost a learner their 2A thread.
    """
    database_url = isolated_database

    asyncio.run(_seed_both_threads(database_url))
    assert asyncio.run(_count(database_url, "messages")) == 2

    run_alembic(database_url, SHAPING_HEAD, downgrade=True)

    assert asyncio.run(_message_contents(database_url)) == {"in a lesson"}

    run_alembic(database_url, MESSAGE_LESSON_HEAD)

    assert asyncio.run(_is_nullable(database_url, "messages", "lesson_id"))
    assert asyncio.run(_message_contents(database_url)) == {"in a lesson"}


# --------------------------------------------------------------------------- #
# AL-321: one applied Change per proposal (migration 0007)
#
# The consent rule — "a Proposal is applied at most once" — moved out of one
# process's lock and into the schema, because a rolling deploy briefly runs two
# machines and neither one's lock excludes the other's. What is worth asserting
# is that the index really is *partial* in both directions: it must reject a
# second **applied** row for one message, and must not stand in the way of the
# two shapes that are legal — an undone row, and the NULL ``message_id`` a
# cleared thread leaves behind (D3).
# --------------------------------------------------------------------------- #


async def _indexes(database_url: str, table: str) -> dict[str, str]:
    """``{index name: its definition}`` for ``table``."""
    connection = await connect(database_url)
    try:
        rows = await connection.fetch(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = $1",
            table,
        )
    finally:
        await connection.close()
    return {row["indexname"]: row["indexdef"] for row in rows}


async def _seed_proposal_message(database_url: str) -> tuple[str, str]:
    """A path with a shaping message on it; returns ``(path_id, message_id)``."""
    connection = await connect(database_url)
    try:
        row = await connection.fetchrow(
            """
            WITH u AS (
                INSERT INTO users (id, issuer, subject, username, display_name)
                VALUES (gen_random_uuid(), 'iss', 'sub-321', 'apply-once', 'A')
                RETURNING id
            ), p AS (
                INSERT INTO paths (id, user_id, topic, level, status)
                SELECT gen_random_uuid(), u.id, 'Rust ownership',
                       'some_experience', 'ready'
                FROM u
                RETURNING id
            ), c AS (
                INSERT INTO conversations (id, path_id, kind)
                SELECT gen_random_uuid(), p.id, 'shaping' FROM p
                RETURNING id, path_id
            )
            INSERT INTO messages (
                id, conversation_id, lesson_id, position, role, content
            )
            SELECT gen_random_uuid(), c.id, NULL, 1, 'tutor', 'a proposal'
            FROM c
            RETURNING id AS message_id,
                      (SELECT path_id FROM c) AS path_id
            """
        )
    finally:
        await connection.close()
    return str(row["path_id"]), str(row["message_id"])


async def _insert_change(
    database_url: str, *, path_id: str, message_id: str | None, status: str
) -> None:
    connection = await connect(database_url)
    try:
        await connection.execute(
            "INSERT INTO path_changes (id, path_id, message_id, kind, payload, status) "
            "VALUES (gen_random_uuid(), $1::uuid, $2::uuid, 'add_lessons', "
            "'{}'::jsonb, $3::path_change_status)",
            path_id,
            message_id,
            status,
        )
    finally:
        await connection.close()


def test_the_applied_change_index_downgrades_and_reapplies_cleanly(
    isolated_database: str,
) -> None:
    """Index-only, both ways: nothing else about ``path_changes`` moves."""
    database_url = isolated_database

    at_head = asyncio.run(_indexes(database_url, CHANGES_TABLE))
    assert APPLIED_CHANGE_INDEX in at_head
    assert "UNIQUE" in at_head[APPLIED_CHANGE_INDEX]
    assert "status = 'applied'" in at_head[APPLIED_CHANGE_INDEX]
    columns = asyncio.run(_columns(database_url, CHANGES_TABLE))

    run_alembic(database_url, MESSAGE_LESSON_HEAD, downgrade=True)

    after_downgrade = asyncio.run(_indexes(database_url, CHANGES_TABLE))
    assert APPLIED_CHANGE_INDEX not in after_downgrade
    assert after_downgrade.keys() | {APPLIED_CHANGE_INDEX} == at_head.keys()
    assert asyncio.run(_columns(database_url, CHANGES_TABLE)) == columns

    run_alembic(database_url, APPLIED_CHANGE_HEAD)

    assert asyncio.run(_indexes(database_url, CHANGES_TABLE)).keys() == at_head.keys()


def test_a_proposal_cannot_be_applied_twice(isolated_database: str) -> None:
    """The rule the index exists for, asserted at the SQL level."""
    database_url = isolated_database
    path_id, message_id = asyncio.run(_seed_proposal_message(database_url))

    asyncio.run(
        _insert_change(
            database_url, path_id=path_id, message_id=message_id, status="applied"
        )
    )

    with pytest.raises(asyncpg.UniqueViolationError):
        asyncio.run(
            _insert_change(
                database_url, path_id=path_id, message_id=message_id, status="applied"
            )
        )


def test_the_index_leaves_undone_rows_and_cleared_threads_alone(
    isolated_database: str,
) -> None:
    """Partial and NULL-tolerant: the two legal shapes still fit.

    ``apply → undo → apply`` is a real sequence (the service refuses the second
    tap for its own product reason, which is not this index's business), and a
    Change whose proposal message a "new conversation" cleared carries a NULL
    ``message_id`` — many of those must coexist, which they do because NULLs are
    never equal to one another.
    """
    database_url = isolated_database
    path_id, message_id = asyncio.run(_seed_proposal_message(database_url))

    asyncio.run(
        _insert_change(
            database_url, path_id=path_id, message_id=message_id, status="undone"
        )
    )
    asyncio.run(
        _insert_change(
            database_url, path_id=path_id, message_id=message_id, status="applied"
        )
    )
    asyncio.run(
        _insert_change(database_url, path_id=path_id, message_id=None, status="applied")
    )
    asyncio.run(
        _insert_change(database_url, path_id=path_id, message_id=None, status="applied")
    )

    assert asyncio.run(_count(database_url, CHANGES_TABLE)) == 4


# --------------------------------------------------------------------------- #
# Path title and Guidance: two additive, nullable columns (migration 0008)
#
# The simplest shape a migration can take — two ``add_column``/``drop_column``
# pairs, no new table, no constraint, no backfill (the migration's own
# docstring: every pre-existing row predates both concepts, so ``NULL`` is the
# honest state, not a placeholder). What is worth asserting is exactly that
# minimalism: only ``paths`` gains columns, only these two, no other table or
# column moves, and a downgrade/reapply round-trip is clean (data-loss in the
# two dropped columns themselves is the documented, expected cost).
# --------------------------------------------------------------------------- #


async def _seed_path_row(database_url: str) -> str:
    """Insert a bare ``paths`` row (with its FK user); returns the path id."""
    connection = await connect(database_url)
    try:
        return str(
            await connection.fetchval(
                """
                WITH u AS (
                    INSERT INTO users (
                        id, issuer, subject, username, display_name
                    )
                    VALUES (
                        gen_random_uuid(), 'iss', 'sub-title', 'title-guidance', 'T'
                    )
                    RETURNING id
                )
                INSERT INTO paths (id, user_id, topic, level, status)
                SELECT gen_random_uuid(), u.id, 'Rust ownership',
                       'some_experience', 'ready'
                FROM u
                RETURNING id
                """
            )
        )
    finally:
        await connection.close()


async def _set_title_and_guidance(
    database_url: str, path_id: str, *, title: str, guidance: str
) -> None:
    connection = await connect(database_url)
    try:
        await connection.execute(
            "UPDATE paths SET title = $2, guidance = $3 WHERE id = $1::uuid",
            path_id,
            title,
            guidance,
        )
    finally:
        await connection.close()


async def _title_and_guidance(
    database_url: str, path_id: str
) -> tuple[str | None, str | None]:
    connection = await connect(database_url)
    try:
        row = await connection.fetchrow(
            "SELECT title, guidance FROM paths WHERE id = $1::uuid", path_id
        )
    finally:
        await connection.close()
    assert row is not None
    return row["title"], row["guidance"]


def test_the_title_and_guidance_step_only_adds_two_columns(
    isolated_database: str,
) -> None:
    """At ``0007`` neither column exists; at head both do. Nothing else moves."""
    database_url = isolated_database

    at_head = asyncio.run(_columns(database_url, "paths"))
    assert {"title", "guidance"} <= at_head

    run_alembic(database_url, APPLIED_CHANGE_HEAD, downgrade=True)

    after_downgrade = asyncio.run(_columns(database_url, "paths"))
    assert after_downgrade == at_head - {"title", "guidance"}

    run_alembic(database_url, TITLE_GUIDANCE_HEAD)

    assert asyncio.run(_columns(database_url, "paths")) == at_head


def test_the_title_and_guidance_step_downgrades_and_reapplies_cleanly(
    isolated_database: str,
) -> None:
    """Applies and reverses (CLAUDE.md pattern): the path row itself survives.

    A downgrade drops the two columns — their contents are lost with them, as
    the migration's own docstring documents — but the ``paths`` row and every
    other column on it are untouched, and reapplying brings the columns back
    (``NULL``, the same "no historical value" state a pre-migration row got).
    """
    database_url = isolated_database
    path_id = asyncio.run(_seed_path_row(database_url))
    asyncio.run(
        _set_title_and_guidance(
            database_url,
            path_id,
            title="Rust ownership, the practical parts",
            guidance="Focus on hands-on examples, skip the history.",
        )
    )
    assert asyncio.run(_title_and_guidance(database_url, path_id)) == (
        "Rust ownership, the practical parts",
        "Focus on hands-on examples, skip the history.",
    )

    run_alembic(database_url, APPLIED_CHANGE_HEAD, downgrade=True)

    after_downgrade = asyncio.run(_columns(database_url, "paths"))
    assert "title" not in after_downgrade
    assert "guidance" not in after_downgrade
    # The row itself, and its other columns, survive the reversal.
    assert asyncio.run(_count(database_url, "paths")) == 1

    run_alembic(database_url, TITLE_GUIDANCE_HEAD)

    # Reapplying is schema-only (no backfill): the columns are back, but empty —
    # the same honest "nothing to compute" state a pre-migration row gets.
    assert asyncio.run(_title_and_guidance(database_url, path_id)) == (None, None)


# --------------------------------------------------------------------------- #
# Phase 5 streaks: the completion-day index (migration 0009)
#
# D1's whole payoff shows up here too: the slice's entire migration is one
# partial index, nothing else — no table, no column, no backfill (TDD §4/D6).
# What is worth asserting mirrors 0007's own index-only step: the index
# appears, is genuinely partial (``WHERE completed_at IS NOT NULL``), and no
# table or column moves either way.
# --------------------------------------------------------------------------- #


def test_the_streak_index_downgrades_and_reapplies_cleanly(
    isolated_database: str,
) -> None:
    """Index-only, both ways: nothing else about ``lessons`` moves."""
    database_url = isolated_database

    at_head = asyncio.run(_indexes(database_url, "lessons"))
    assert STREAK_INDEX_NAME in at_head
    assert "completed_at IS NOT NULL" in at_head[STREAK_INDEX_NAME]
    columns = asyncio.run(_columns(database_url, "lessons"))

    run_alembic(database_url, TITLE_GUIDANCE_HEAD, downgrade=True)

    after_downgrade = asyncio.run(_indexes(database_url, "lessons"))
    assert STREAK_INDEX_NAME not in after_downgrade
    assert after_downgrade.keys() | {STREAK_INDEX_NAME} == at_head.keys()
    assert asyncio.run(_columns(database_url, "lessons")) == columns

    run_alembic(database_url, STREAK_INDEX_HEAD)

    assert asyncio.run(_indexes(database_url, "lessons")).keys() == at_head.keys()


def test_earlier_tables_are_unchanged_by_the_streak_index_migration(
    isolated_database: str,
) -> None:
    """The step adds one index; it alters no table or column."""
    database_url = isolated_database
    tracked = (*PHASE_1_TABLES, *PHASE_2_TABLES, FLAGS_TABLE, CHANGES_TABLE)

    before = {table: asyncio.run(_columns(database_url, table)) for table in tracked}
    run_alembic(database_url, TITLE_GUIDANCE_HEAD, downgrade=True)
    after = {table: asyncio.run(_columns(database_url, table)) for table in tracked}

    assert before == after


# --------------------------------------------------------------------------- #
# Phase 3: flashcards (migration 0010)
#
# Three new tables — D1's two (``flashcards``, ``flashcard_reviews``) plus the
# sparse ``flashcard_draft_runs`` claim row (D7) — and two new enum types.
# ``0010`` is the only migration in the repo whose downgrade drops enum types
# alongside its tables (TDD §4), which is exactly the failure a reapply test
# catches: an enum type left behind (or a drop that races the tables using it)
# would collide with, or break, the next upgrade. Mirrors the tutor branch's
# own enum-drop assertions (``0003``).
# --------------------------------------------------------------------------- #

FLASHCARDS_HEAD = "0010_flashcards"
FLASHCARDS_TABLES = ("flashcards", "flashcard_reviews", "flashcard_draft_runs")
FLASHCARDS_ENUM_TYPES = ("flashcard_grade", "flashcard_draft_run_state")


def test_flashcards_migration_downgrades_and_reapplies_cleanly(
    isolated_database: str,
) -> None:
    database_url = isolated_database

    at_head = asyncio.run(_tables(database_url))
    assert set(FLASHCARDS_TABLES) <= at_head

    username = asyncio.run(_seed_phase_1_row(database_url))

    run_alembic(database_url, STREAK_INDEX_HEAD, downgrade=True)

    after_downgrade = asyncio.run(_tables(database_url))
    assert set(FLASHCARDS_TABLES) & after_downgrade == set()
    # Everything before Phase 3 survives the reversal, rows included.
    assert set(PHASE_1_TABLES) <= after_downgrade
    assert set(PHASE_2_TABLES) <= after_downgrade
    assert FLAGS_TABLE in after_downgrade
    assert CHANGES_TABLE in after_downgrade
    assert asyncio.run(_count(database_url, "users")) == 1
    # The two Phase 3 enum types go with the step — no orphan type left behind
    # to collide with a re-upgrade. This is the property this test exists for.
    assert set(FLASHCARDS_ENUM_TYPES) & asyncio.run(_enum_types(database_url)) == set()
    # Earlier phases' enums are not collateral damage.
    assert {
        "message_role",
        "message_source",
        "level",
        "path_status",
        "conversation_kind",
        "path_change_kind",
        "path_change_status",
    } <= asyncio.run(_enum_types(database_url))

    run_alembic(database_url, FLASHCARDS_HEAD)

    reapplied = asyncio.run(_tables(database_url))
    assert set(FLASHCARDS_TABLES) <= reapplied
    assert set(PHASE_1_TABLES) <= reapplied
    assert asyncio.run(_count(database_url, "users")) == 1
    assert username == "migration-user"
    # The tables come back empty — a migration reapply is schema-only.
    for table in FLASHCARDS_TABLES:
        assert asyncio.run(_count(database_url, table)) == 0
    assert set(FLASHCARDS_ENUM_TYPES) <= asyncio.run(_enum_types(database_url))


def test_flashcards_migration_creates_the_documented_columns(
    isolated_database: str,
) -> None:
    """Asserted at ``0010`` (``FLASHCARDS_HEAD``), not at ``head``: ``0011``
    (AL-410) adds two more columns to ``flashcards``, so this step's own
    column set is only stable if bracketed rather than read off whatever
    ``head`` happens to be — the same "a step is asserted from its own
    vantage point" rule the module docstring states.
    """
    database_url = isolated_database
    run_alembic(database_url, FLASHCARDS_HEAD, downgrade=True)

    assert asyncio.run(_columns(database_url, "flashcards")) == {
        "id",
        "user_id",
        "front",
        "back",
        "kept_at",
        "rung",
        "due_on",
        "source_lesson_id",
        "source_path_id",
        "source_lesson_title",
        "source_path_title",
        "source_generated_at",
        "created_at",
        "updated_at",
    }
    assert asyncio.run(_columns(database_url, "flashcard_reviews")) == {
        "id",
        "card_id",
        "user_id",
        "grade",
        "reviewed_at",
        "local_day",
        "rung_before",
        "rung_after",
        "due_on_before",
        "due_on_after",
        "created_at",
        "updated_at",
    }
    assert asyncio.run(_columns(database_url, "flashcard_draft_runs")) == {
        "lesson_id",
        "state",
        "started_at",
        "error",
        "created_at",
        "updated_at",
    }


def test_earlier_tables_are_unchanged_by_the_flashcards_migration(
    isolated_database: str,
) -> None:
    """The Phase 3 branch adds three tables; it alters none of the existing ones."""
    database_url = isolated_database
    tracked = (
        *PHASE_1_TABLES,
        *PHASE_2_TABLES,
        FLAGS_TABLE,
        CHANGES_TABLE,
        "lessons",
        "paths",
    )

    before = {table: asyncio.run(_columns(database_url, table)) for table in tracked}
    run_alembic(database_url, STREAK_INDEX_HEAD, downgrade=True)
    after = {table: asyncio.run(_columns(database_url, table)) for table in tracked}

    assert before == after


# --------------------------------------------------------------------------- #
# AL-410 (issue #156): card management (migration 0011)
#
# Two nullable columns (`deleted_at`, `edited_at`) plus a rewritten partial
# index and one new one — the migration's own docstring records *why* (soft
# delete protects the streak/D1's replay guarantee; edit provenance protects
# the eval sample). What is worth asserting mirrors every other pure-schema
# step in this file: the columns appear/disappear cleanly, the rewritten
# index's *predicate* really does widen (not just survive under the same
# name), the new index is genuinely partial, and no other table moves either
# way.
# --------------------------------------------------------------------------- #

CARD_MANAGEMENT_HEAD = "0011_flashcard_management"
DUE_ON_INDEX = "ix_flashcards_user_id_due_on"
KEPT_AT_INDEX = "ix_flashcards_user_id_kept_at"


def test_the_card_management_migration_downgrades_and_reapplies_cleanly(
    isolated_database: str,
) -> None:
    database_url = isolated_database

    at_head = asyncio.run(_columns(database_url, "flashcards"))
    assert {"deleted_at", "edited_at"} <= at_head
    at_head_indexes = asyncio.run(_indexes(database_url, "flashcards"))
    assert KEPT_AT_INDEX in at_head_indexes
    assert "kept_at IS NOT NULL" in at_head_indexes[DUE_ON_INDEX]
    assert "deleted_at IS NULL" in at_head_indexes[DUE_ON_INDEX]

    username = asyncio.run(_seed_phase_1_row(database_url))

    run_alembic(database_url, FLASHCARDS_HEAD, downgrade=True)

    after_downgrade = asyncio.run(_columns(database_url, "flashcards"))
    assert after_downgrade == at_head - {"deleted_at", "edited_at"}
    after_indexes = asyncio.run(_indexes(database_url, "flashcards"))
    assert KEPT_AT_INDEX not in after_indexes
    # The old, narrower predicate comes back exactly — not left widened.
    assert "kept_at IS NOT NULL" in after_indexes[DUE_ON_INDEX]
    assert "deleted_at" not in after_indexes[DUE_ON_INDEX]
    assert asyncio.run(_count(database_url, "users")) == 1

    run_alembic(database_url, CARD_MANAGEMENT_HEAD)

    reapplied = asyncio.run(_columns(database_url, "flashcards"))
    assert reapplied == at_head
    reapplied_indexes = asyncio.run(_indexes(database_url, "flashcards"))
    assert KEPT_AT_INDEX in reapplied_indexes
    assert "kept_at IS NOT NULL" in reapplied_indexes[DUE_ON_INDEX]
    assert "deleted_at IS NULL" in reapplied_indexes[DUE_ON_INDEX]
    assert asyncio.run(_count(database_url, "users")) == 1
    assert username == "migration-user"


def test_the_card_management_migration_widens_the_due_on_index_predicate(
    isolated_database: str,
) -> None:
    """The step this test is named for: ``0011`` does not merely add a
    column, it **rewrites** the one index the daily selection's hot path
    actually uses, so a soft-deleted card cannot linger in it."""
    database_url = isolated_database

    run_alembic(database_url, FLASHCARDS_HEAD, downgrade=True)
    before_indexes = asyncio.run(_indexes(database_url, "flashcards"))
    assert "kept_at IS NOT NULL" in before_indexes[DUE_ON_INDEX]
    assert "deleted_at" not in before_indexes[DUE_ON_INDEX]

    run_alembic(database_url, CARD_MANAGEMENT_HEAD)

    after_indexes = asyncio.run(_indexes(database_url, "flashcards"))
    assert "kept_at IS NOT NULL" in after_indexes[DUE_ON_INDEX]
    assert "deleted_at IS NULL" in after_indexes[DUE_ON_INDEX]


def test_the_new_kept_at_index_is_partial_and_descending(
    isolated_database: str,
) -> None:
    """The card list's own ordering index (§2): partial like the rewritten
    one above, and genuinely ``DESC`` — a plain ascending index would not
    match `ORDER BY kept_at DESC, id DESC`."""
    database_url = isolated_database

    indexdef = asyncio.run(_indexes(database_url, "flashcards"))[KEPT_AT_INDEX]

    assert "user_id" in indexdef
    assert "kept_at" in indexdef
    assert "DESC" in indexdef
    assert "kept_at IS NOT NULL" in indexdef
    assert "deleted_at IS NULL" in indexdef


def test_earlier_tables_are_unchanged_by_the_card_management_migration(
    isolated_database: str,
) -> None:
    """The step adds two columns and rewrites/adds indexes on ``flashcards``
    alone; it alters no other table."""
    database_url = isolated_database
    tracked = (
        *PHASE_1_TABLES,
        *PHASE_2_TABLES,
        FLAGS_TABLE,
        CHANGES_TABLE,
        "lessons",
        "paths",
        "flashcard_reviews",
        "flashcard_draft_runs",
    )

    before = {table: asyncio.run(_columns(database_url, table)) for table in tracked}
    run_alembic(database_url, FLASHCARDS_HEAD, downgrade=True)
    after = {table: asyncio.run(_columns(database_url, table)) for table in tracked}

    assert before == after
