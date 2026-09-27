"""Teaching style: one additive, nullable column on ``paths``.

Adds `docs/CONTEXT.md`'s **Teaching style** — the learner's standing instruction
about *how* a path's lessons are taught — as ``paths.teaching_style``.

Unlike its neighbour ``guidance`` (0008) the column is **mutable** after
creation (``PUT /paths/{id}/teaching-style``): lesson generation reads it when
each lesson runs, so an edit reaches the next lesson generated and never an
already-written one. ``NULL``-able ``TEXT`` with **no backfill**: every existing
path was created before the concept existed, and "no style" is its true state.

Additive and column-only, so the downgrade is one plain ``drop_column``.

Revision ID: 0015_path_teaching_style
Revises: 0014_user_settings
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0015_path_teaching_style"
down_revision: str | None = "0014_user_settings"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column("paths", sa.Column("teaching_style", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("paths", "teaching_style")
