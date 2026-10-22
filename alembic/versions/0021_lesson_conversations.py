"""Scope tutor conversations to lessons; retain path-scoped shaping history.

Revision ID: 0021_lesson_conversations
Revises: 0020_reminder_scan_progress
"""

import sqlalchemy as sa

from alembic import op

revision = "0021_lesson_conversations"
down_revision = "0020_reminder_scan_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing lesson history is intentionally discarded, not reassigned.
    # Shaping conversations and path Change history are untouched.
    op.execute("DELETE FROM conversations WHERE kind = 'lesson'")
    op.add_column("conversations", sa.Column("lesson_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_conversations_lesson_id",
        "conversations",
        "lessons",
        ["lesson_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("uq_conversations_path_kind", "conversations", type_="unique")
    op.create_unique_constraint(
        "uq_conversations_scope",
        "conversations",
        ["path_id", "kind", "lesson_id"],
        postgresql_nulls_not_distinct=True,
    )
    op.create_check_constraint(
        "ck_conversations_scope",
        "conversations",
        "(kind = 'lesson' AND lesson_id IS NOT NULL) OR "
        "(kind = 'shaping' AND lesson_id IS NULL)",
    )


def downgrade() -> None:
    # Multiple lesson threads cannot truthfully become a single old thread.
    op.execute("DELETE FROM conversations WHERE kind = 'lesson'")
    op.drop_constraint("ck_conversations_scope", "conversations", type_="check")
    op.drop_constraint("uq_conversations_scope", "conversations", type_="unique")
    op.drop_column("conversations", "lesson_id")
    op.create_unique_constraint(
        "uq_conversations_path_kind",
        "conversations",
        ["path_id", "kind"],
    )
