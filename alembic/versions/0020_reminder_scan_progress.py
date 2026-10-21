"""Persist Reminder scan progress across timed invocations.

Revision ID: 0020_reminder_scan_progress
Revises: 0019_beat_scan_progress
"""

import sqlalchemy as sa

from alembic import op

revision = "0020_reminder_scan_progress"
down_revision = "0019_beat_scan_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reminder_scan_progress",
        sa.Column("singleton", sa.Boolean(), primary_key=True),
        sa.Column("after", sa.Uuid(), nullable=True),
        sa.CheckConstraint("singleton", name="ck_reminder_scan_singleton"),
    )
    op.execute(sa.text("INSERT INTO reminder_scan_progress (singleton) VALUES (true)"))


def downgrade() -> None:
    op.drop_table("reminder_scan_progress")
