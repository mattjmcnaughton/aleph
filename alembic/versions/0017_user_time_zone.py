"""Store the last observed learner time zone.

Revision ID: 0017_user_time_zone
Revises: 0016_remove_flashcards
"""

import sqlalchemy as sa

from alembic import op

revision = "0017_user_time_zone"
down_revision = "0016_remove_flashcards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("time_zone", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column("time_zone_seen_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "time_zone_seen_at")
    op.drop_column("users", "time_zone")
