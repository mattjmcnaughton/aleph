"""Quick check Grading keys and learner Reasonings.

``quick_checks.grading_key`` (JSONB, nullable) holds the **Grading key** the
lesson call writes beside each new Quick check; checks generated earlier keep
null and ask for no Reasoning. ``reasonings`` stores the learner's own-words
"why" for their first Attempt (one per Attempt, cascading with it), the
grader's assessment, and the one follow-up answer. Purely additive, so the
downgrade is a clean reversal.

Revision ID: 0027_quick_check_explanations
Revises: 0026_linked_material
Create Date: 2026-11-27
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0027_quick_check_explanations"
down_revision: str | None = "0026_linked_material"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column(
        "quick_checks",
        sa.Column(
            "grading_key", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )
    op.create_table(
        "reasonings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("assessment", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "grading_failed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("follow_up_answer", sa.Text(), nullable=True),
        sa.Column(
            "follow_up_assessment",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "follow_up_grading_failed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
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
        sa.ForeignKeyConstraint(["attempt_id"], ["attempts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id", name="uq_reasonings_attempt"),
    )


def downgrade() -> None:
    op.drop_table("reasonings")
    op.drop_column("quick_checks", "grading_key")
