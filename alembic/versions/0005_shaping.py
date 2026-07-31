"""The shaping branch: a second conversation kind, proposals, and Changes.

Creates the Phase 2B schema (TDD §4/D3):

* ``conversations.kind`` (``lesson | shaping``), and the widened
  ``UNIQUE (path_id, kind)`` replacing ``UNIQUE (path_id)`` — two threads per
  path, never two of either. The column is added **with**
  ``DEFAULT 'lesson' NOT NULL``, so every existing 2A conversation is backfilled
  by the ``ALTER`` itself; the constraint is only swapped afterwards, and all of
  it runs in one transaction (§12 — safe on Neon at this table's size).
* ``messages.proposal`` — the tutor's validated edit plan, carried exactly as
  ``tutor_check`` is. Applicability (tutor rows, shaping threads only) is
  **app-enforced, deliberately not a CHECK constraint**, like ``source`` and
  ``tutor_check`` before it (Phase 2 TDD §4).
* ``lessons.revision_instruction`` — set by ``apply_change`` for a **Revision**,
  cleared when the lesson reaches ``generated`` again (D7).
* ``path_changes`` — the Change history. ``path_id`` cascades;
  ``message_id`` is **``SET NULL``, not cascade** (D3), because "new
  conversation" deletes messages and the history must survive it: history
  belongs to the path, not to the conversation.

Additive apart from the widened conversation uniqueness, so the downgrade is a
clean reversal. Narrowing back to ``UNIQUE (path_id)`` requires at most one
conversation per path, so the reversal drops ``shaping`` threads first — rows
that could not exist before this migration.

Revision ID: 0005_shaping
Revises: 0004_user_feature_overrides
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_shaping"
down_revision: str | None = "0004_user_feature_overrides"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

conversation_kind = postgresql.ENUM(
    "lesson",
    "shaping",
    name="conversation_kind",
    create_type=False,
)
path_change_kind = postgresql.ENUM(
    "add_lessons",
    "revise_lesson",
    name="path_change_kind",
    create_type=False,
)
path_change_status = postgresql.ENUM(
    "applied",
    "undone",
    name="path_change_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    conversation_kind.create(bind, checkfirst=True)
    path_change_kind.create(bind, checkfirst=True)
    path_change_status.create(bind, checkfirst=True)

    # Backfill first, swap the constraint second — both inside this migration's
    # single transaction. The ``server_default`` is what backfills: every
    # pre-2B row becomes ``'lesson'`` as the column is added, so the widened
    # unique constraint can never find a NULL to choke on.
    op.add_column(
        "conversations",
        sa.Column(
            "kind",
            conversation_kind,
            nullable=False,
            server_default="lesson",
        ),
    )
    op.drop_constraint("uq_conversations_path", "conversations", type_="unique")
    op.create_unique_constraint(
        "uq_conversations_path_kind",
        "conversations",
        ["path_id", "kind"],
    )

    op.add_column(
        "messages",
        sa.Column("proposal", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "lessons",
        sa.Column("revision_instruction", sa.Text(), nullable=True),
    )

    op.create_table(
        "path_changes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("path_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("kind", path_change_kind, nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            path_change_status,
            nullable=False,
            server_default="applied",
        ),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
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
        # SET NULL, not cascade: clearing a thread must not erase history (D3).
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_path_changes_path_id", "path_changes", ["path_id"])
    op.create_index("ix_path_changes_message_id", "path_changes", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_path_changes_message_id", table_name="path_changes")
    op.drop_index("ix_path_changes_path_id", table_name="path_changes")
    op.drop_table("path_changes")

    op.drop_column("lessons", "revision_instruction")
    op.drop_column("messages", "proposal")

    op.drop_constraint("uq_conversations_path_kind", "conversations", type_="unique")
    # ``UNIQUE (path_id)`` cannot hold while a path has both threads. Shaping
    # threads are exactly the rows this migration made possible, so dropping
    # them is the reversal (data-loss-on-downgrade, the standard posture — §12);
    # the cascade takes their messages, and Phase 2A's ``lesson`` threads are
    # untouched.
    op.execute("DELETE FROM conversations WHERE kind = 'shaping'")
    op.drop_column("conversations", "kind")
    op.create_unique_constraint("uq_conversations_path", "conversations", ["path_id"])

    bind = op.get_bind()
    path_change_status.drop(bind, checkfirst=True)
    path_change_kind.drop(bind, checkfirst=True)
    conversation_kind.drop(bind, checkfirst=True)
