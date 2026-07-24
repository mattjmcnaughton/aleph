"""Admin model-picker overrides on paths.

Adds the nullable ``model_outline`` / ``model_lesson`` columns to ``paths`` so an
admin's per-path model selection (AL-052, TDD §5.3/D14) is stored on the row and
survives the DB-driven resume/reconcile (§5.4/D6). ``NULL`` means "use the
configured slot" — the default for every existing row and every un-overridden
create — so the change is purely additive and needs no backfill.

Revision ID: 0002_path_model_overrides
Revises: 0001_initial_schema
Create Date: 2026-07-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0002_path_model_overrides"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column("paths", sa.Column("model_outline", sa.Text(), nullable=True))
    op.add_column("paths", sa.Column("model_lesson", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("paths", "model_lesson")
    op.drop_column("paths", "model_outline")
