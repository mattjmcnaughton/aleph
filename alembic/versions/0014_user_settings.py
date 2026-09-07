"""Per-learner settings: ``user_settings`` (CONTEXT.md: Settings / Auto-draft).

The first learner-controlled setting — **Auto-draft**, whether Aleph drafts
flashcards as a lesson opens (Phase 3 TDD D5) or only when asked — and the
table every later setting lands in as one more column.

One row per account, keyed on ``user_id`` (natural key as primary key, on the
``user_feature_overrides`` precedent: a plain ``ON CONFLICT`` upsert, at most
one row per learner). A learner who never opens Settings has **no row** and
resolves to the code defaults at read time, so nothing is backfilled here and
no existing account changes behaviour: ``auto_draft_flashcards`` defaults to
``true`` both as the column's server default and in the service's view, and
the launched Phase 3 experience stays exactly what every learner sees until
they choose otherwise.

``ondelete=CASCADE`` keeps the table free of orphans when accounts are deleted.

Strictly additive: no existing table is touched, so the downgrade is a clean
reversal (drop the one table) that leaves everything before it exactly as
``0013`` left it.

Revision ID: 0014_user_settings
Revises: 0013_beat_research_runs
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0014_user_settings"
down_revision: str | None = "0013_beat_research_runs"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "auto_draft_flashcards",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_settings")
