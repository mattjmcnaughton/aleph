"""Independent opt-in web search for outline and lesson generation.

Revision ID: 0022_path_web_search
Revises: 0021_lesson_conversations
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0022_path_web_search"
down_revision: str | None = "0021_lesson_conversations"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    for name in ("search_outline", "search_lessons"):
        op.add_column(
            "paths",
            sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    op.drop_column("paths", "search_lessons")
    op.drop_column("paths", "search_outline")
