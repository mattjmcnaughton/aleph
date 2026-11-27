"""Papers as Material: each paper's digest and each lesson's concept ids.

docs/tdds/papers-material.md D7, D8. ``materials.digest`` holds a paper's core
concepts and prerequisites, written once by the outline task before the
outline runs, so a retried outline reuses it. ``lessons.material_concepts``
holds the digest concept ids a lesson teaches; prep lessons store none. Both
are nullable and null for every existing row. Purely additive; the downgrade
is a clean reversal.

Revision ID: 0030_paper_digests
Revises: 0029_mcp_oauth
Create Date: 2026-11-27
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0030_paper_digests"
down_revision: str | None = "0029_mcp_oauth"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column(
        "materials",
        sa.Column("digest", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "lessons",
        sa.Column("material_concepts", sa.ARRAY(sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("lessons", "material_concepts")
    op.drop_column("materials", "digest")
