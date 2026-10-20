"""Web Push Devices, preferences and daily Reminder claims.

Revision ID: 0018_web_push
Revises: 0017_user_time_zone
"""

import sqlalchemy as sa

from alembic import op

revision = "0018_web_push"
down_revision = "0017_user_time_zone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("endpoint", sa.Text(), nullable=False, unique=True),
        sa.Column("p256dh", sa.Text(), nullable=False),
        sa.Column("auth", sa.Text(), nullable=False),
        sa.Column("user_agent", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_registered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_devices_user_id", "devices", ["user_id"])
    op.create_table(
        "notification_preferences",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "reminder_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "reminder_hour",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("20"),
        ),
        sa.Column(
            "path_ready_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "brief_published_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.CheckConstraint(
            "reminder_hour BETWEEN 0 AND 23", name="ck_notification_reminder_hour"
        ),
    )
    op.create_table(
        "reminder_sends",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("local_date", sa.Date(), primary_key=True),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("outcome", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("reminder_sends")
    op.drop_table("notification_preferences")
    op.drop_table("devices")
