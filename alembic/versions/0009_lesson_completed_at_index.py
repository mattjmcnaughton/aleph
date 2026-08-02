"""One partial index for the streak query: ``lessons(path_id, completed_at)`` (D6).

The Streaks slice's whole migration (TDD §4/D1): no table, no backfill, no
column — the feature is a ``GROUP BY`` over rows that already exist
(``LessonRepository.completion_days_for_user``, §5.2), and this index is what
makes that scan cheap.

``ix_paths_user_id`` already seeks the learner's paths (the query's outer
side); this index covers the inner side — the join and the group-by both read
``path_id`` and ``completed_at`` and nothing else, so an index-only scan is
available whenever the visibility map is warm. **Partial**, `WHERE completed_at
IS NOT NULL`, because most lessons on a growing path are incomplete and the
query only ever wants the completed ones — an unfiltered index would spend most
of its size on rows the query never touches.

Written in the ``0007_applied_change_uniqueness`` style
(``op.create_index(..., postgresql_where=…)``). Online-safe on Neon at this
table's size; ``CONCURRENTLY`` is not used because Alembic runs migrations in a
transaction and the table is small — if it ever isn't, that is a one-line change
with ``autocommit_block()`` (TDD §4).

The index is declared on the model too (``Lesson.__table_args__``), so
``tests/integration/test_schema.py`` — which asserts model/DDL agreement —
keeps the two honest.

Additive and index-only: no column, no data, no constraint on an existing
column, and the downgrade drops just this index.

Revision ID: 0009_lesson_completed_at_index
Revises: 0008_path_title_and_guidance
Create Date: 2026-08-02
"""

from __future__ import annotations

from alembic import op

revision: str = "0009_lesson_completed_at_index"
down_revision: str | None = "0008_path_title_and_guidance"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

INDEX_NAME = "ix_lessons_path_id_completed_at"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "lessons",
        ["path_id", "completed_at"],
        postgresql_where="completed_at IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="lessons")
