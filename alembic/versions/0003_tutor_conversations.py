"""The tutor's conversation branch.

Creates the Phase 2 schema (TDD §4/D3): ``conversations`` (one per path,
``UNIQUE (path_id)``) and ``messages`` (``position`` totally ordering the thread,
``role``/``source`` enums, the ``tutor_check`` JSONB payload), hanging off
``paths`` and ``lessons`` with ``ON DELETE CASCADE`` so "new conversation" and
"delete path" need no application code.

Strictly additive: Phase 1's tables are unchanged and un-migrated, so the
downgrade is a clean reversal (drop the two tables and the two new enum types)
that leaves Phase 1 exactly as it was.

Column applicability is by role and enforced in the application, deliberately
**not** by CHECK constraints (TDD §4): ``source`` belongs on learner rows,
``tutor_check`` on tutor rows.

Revision ID: 0003_tutor_conversations
Revises: 0002_path_model_overrides
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_tutor_conversations"
down_revision: str | None = "0002_path_model_overrides"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

message_role = postgresql.ENUM(
    "learner",
    "tutor",
    name="message_role",
    create_type=False,
)
message_source = postgresql.ENUM(
    "typed",
    "suggestion",
    name="message_source",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    message_role.create(bind, checkfirst=True)
    message_source.create(bind, checkfirst=True)

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("path_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["path_id"], ["paths.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path_id", name="uq_conversations_path"),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("role", message_role, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("lesson_id", sa.Uuid(), nullable=False),
        sa.Column("source", message_source, nullable=True),
        sa.Column(
            "tutor_check", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "position",
            name="uq_messages_conversation_position",
        ),
    )
    op.create_index("ix_messages_lesson_id", "messages", ["lesson_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_lesson_id", table_name="messages")
    op.drop_table("messages")
    op.drop_table("conversations")

    bind = op.get_bind()
    message_source.drop(bind, checkfirst=True)
    message_role.drop(bind, checkfirst=True)
