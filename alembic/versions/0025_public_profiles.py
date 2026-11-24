"""Public profiles: a Handle, the profile switches, Shared items and credit.

``users.handle`` is the learner-chosen name in ``/@handle`` (CONTEXT.md:
*Handle*): unique, checked against the URL grammar, and written once (TDD D8).
``profile_public`` and ``profile_show_activity`` are the two owner switches.
``paths.shared`` / ``beats.shared`` mark what the profile shows; turning the
profile off leaves them as they are (PRD §4.1). ``source_path_id`` /
``source_beat_id`` are non-authoritative credit (TDD D7), cleared when the
source is deleted. Purely additive, so the downgrade is a clean reversal.

Revision ID: 0025_public_profiles
Revises: 0024_lesson_visuals
Create Date: 2026-11-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0025_public_profiles"
down_revision: str | None = "0024_lesson_visuals"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("handle", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "profile_public", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "profile_show_activity",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.create_unique_constraint("uq_users_handle", "users", ["handle"])
    op.create_check_constraint(
        "ck_users_handle_format", "users", "handle ~ '^[a-z0-9-]{3,30}$'"
    )

    for table, source in (("paths", "source_path_id"), ("beats", "source_beat_id")):
        op.add_column(
            table,
            sa.Column(
                "shared", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
        )
        op.add_column(table, sa.Column(source, sa.Uuid(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_{source}", table, table, [source], ["id"], ondelete="SET NULL"
        )
        op.create_index(
            f"ix_{table}_user_id_shared",
            table,
            ["user_id"],
            postgresql_where=sa.text("shared"),
        )


def downgrade() -> None:
    for table, source in (("paths", "source_path_id"), ("beats", "source_beat_id")):
        op.drop_index(f"ix_{table}_user_id_shared", table_name=table)
        op.drop_constraint(f"fk_{table}_{source}", table, type_="foreignkey")
        op.drop_column(table, source)
        op.drop_column(table, "shared")
    op.drop_constraint("ck_users_handle_format", "users", type_="check")
    op.drop_constraint("uq_users_handle", "users", type_="unique")
    op.drop_column("users", "profile_show_activity")
    op.drop_column("users", "profile_public")
    op.drop_column("users", "handle")
