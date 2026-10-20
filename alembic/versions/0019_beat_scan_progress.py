"""Persist scheduled Beat scan admission across timed invocations.

Revision ID: 0019_beat_scan_progress
Revises: 0018_web_push
"""

import sqlalchemy as sa

from alembic import op

revision = "0019_beat_scan_progress"
down_revision = "0018_web_push"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "beat_scan_progress",
        sa.Column("singleton", sa.Boolean(), primary_key=True),
        sa.Column("after", sa.Uuid(), nullable=True),
        sa.CheckConstraint("singleton", name="ck_beat_scan_singleton"),
    )
    op.execute(sa.text("INSERT INTO beat_scan_progress (singleton) VALUES (true)"))


def downgrade() -> None:
    op.drop_table("beat_scan_progress")
