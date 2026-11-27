"""Linked Material: a path's Material snapshot and each lesson's grounding plan.

Creates ``materials`` and ``material_sections`` (docs/tdds/linked-material.md),
both cascading from ``paths``, and adds two nullable columns:
``paths.material_mode`` and ``lessons.material_sections``. Purely additive, and
touches no table from the Quick check explanations migration, so the two
sprint migrations may land in either order; the downgrade is a clean reversal.

Revision ID: 0026_linked_material
Revises: 0025_public_profiles
Create Date: 2026-11-27
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0026_linked_material"
down_revision: str | None = "0025_public_profiles"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def _audit_columns() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.add_column(
        "paths",
        sa.Column("material_mode", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_paths_material_mode",
        "paths",
        "material_mode IS NULL OR material_mode IN ('teach', 'start')",
    )
    op.add_column(
        "lessons",
        sa.Column("material_sections", sa.ARRAY(sa.Integer()), nullable=True),
    )
    op.create_table(
        "materials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("path_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["path_id"], ["paths.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path_id", "position"),
    )
    op.create_table(
        "material_sections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("path_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["path_id"], ["paths.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path_id", "position"),
    )
    op.create_index(
        "ix_material_sections_material_id", "material_sections", ["material_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_material_sections_material_id", table_name="material_sections")
    op.drop_table("material_sections")
    op.drop_table("materials")
    op.drop_column("lessons", "material_sections")
    op.drop_constraint("ck_paths_material_mode", "paths", type_="check")
    op.drop_column("paths", "material_mode")
