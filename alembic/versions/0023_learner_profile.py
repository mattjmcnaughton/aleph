"""About you on the account, and one stored Learning summary per account.

``users.about_you`` is the learner's optional free text every generation reads
(CONTEXT.md: *About you*). ``learning_summaries`` holds at most one row per
account (natural key as primary key): the last written summary ``body`` and the
state of the run writing the next one. ``started_at`` is the fence a run's
completion must still match, so a stale or superseded run cannot overwrite a
newer one.

Revision ID: 0023_learner_profile
Revises: 0022_path_web_search
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0023_learner_profile"
down_revision: str | None = "0022_path_web_search"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("about_you", sa.Text(), nullable=True))
    op.create_table(
        "learning_summaries",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body", postgresql.JSONB(), nullable=True),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('generating', 'ready', 'failed')",
            name="ck_learning_summaries_status",
        ),
        sa.CheckConstraint(
            "(body IS NULL) = (written_at IS NULL)",
            name="ck_learning_summaries_body_written",
        ),
    )


def downgrade() -> None:
    op.drop_table("learning_summaries")
    op.drop_column("users", "about_you")
