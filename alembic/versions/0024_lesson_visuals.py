"""Interactive lesson visuals, stored beside the Read passage.

Creates ``lesson_visuals`` (docs/tdds/lesson-visuals.md): one row per **Visual**,
its UUID the identifier the passage's ```visual <id> fence names. ``lesson_id``
cascades, so deleting a lesson or path takes its visuals with it. Purely
additive, so the downgrade is a clean reversal.

Revision ID: 0024_lesson_visuals
Revises: 0023_learner_profile
Create Date: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0024_lesson_visuals"
down_revision: str | None = "0023_learner_profile"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

lesson_visual_kind = postgresql.ENUM(
    "interactive",
    name="lesson_visual_kind",
    create_type=False,
)


def upgrade() -> None:
    lesson_visual_kind.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "lesson_visuals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lesson_id", sa.Uuid(), nullable=False),
        sa.Column("kind", lesson_visual_kind, nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("alt", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("kit_version", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lesson_visuals_lesson_id", "lesson_visuals", ["lesson_id"])


def downgrade() -> None:
    op.drop_index("ix_lesson_visuals_lesson_id", table_name="lesson_visuals")
    op.drop_table("lesson_visuals")
    lesson_visual_kind.drop(op.get_bind(), checkfirst=True)
