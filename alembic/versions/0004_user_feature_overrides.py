"""Per-user feature-flag overrides: ``user_feature_overrides``.

Feature flags are defined in code with their defaults
(``aleph.services.feature_flags``); the database stores only per-user
exceptions. A row means "for this user, this flag is forced to ``enabled``";
absence of a row means "use the default". Keying the table on
``(user_id, flag_key)`` makes the admin upsert natural and caps the table at one
row per user per flag. ``flag_key`` is a plain string rather than an enum so
adding or removing a flag in code never requires a schema change — rows for
flags that no longer exist are simply ignored at resolution time.

``ondelete=CASCADE`` keeps the table free of orphans when accounts are deleted.

Strictly additive: Phase 1's tables and the tutor branch's (``0003``) are
untouched, so the downgrade is a clean reversal (drop the one table) that leaves
everything before it exactly as it was.

Ported from habagou's ``0008_user_feature_overrides``.

Revision ID: 0004_user_feature_overrides
Revises: 0003_tutor_conversations
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0004_user_feature_overrides"
down_revision: str | None = "0003_tutor_conversations"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "user_feature_overrides",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("flag_key", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "flag_key"),
    )


def downgrade() -> None:
    op.drop_table("user_feature_overrides")
