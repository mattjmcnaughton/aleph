"""Remove flashcard and learner-settings persistence.

This migration intentionally destroys cards, reviews, draft runs, learner
settings, and the retired flashcards feature overrides. Its downgrade restores
the 0014 schema only; deleted rows cannot be reconstructed.

Revision ID: 0015_remove_flashcards
Revises: 0014_user_settings
Create Date: 2026-09-20
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015_remove_flashcards"
down_revision: str | None = "0014_user_settings"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

flashcard_grade = postgresql.ENUM(
    "again", "got_it", name="flashcard_grade", create_type=False
)
flashcard_draft_run_state = postgresql.ENUM(
    "generating",
    "generated",
    "failed",
    name="flashcard_draft_run_state",
    create_type=False,
)


def upgrade() -> None:
    op.execute(
        sa.text("DELETE FROM user_feature_overrides WHERE flag_key = 'flashcards'")
    )
    op.drop_table("flashcard_reviews")
    op.drop_table("flashcard_draft_runs")
    op.drop_table("flashcards")
    op.drop_table("user_settings")

    bind = op.get_bind()
    flashcard_grade.drop(bind, checkfirst=True)
    flashcard_draft_run_state.drop(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    flashcard_grade.create(bind, checkfirst=True)
    flashcard_draft_run_state.create(bind, checkfirst=True)

    op.create_table(
        "flashcards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("front", sa.Text(), nullable=False),
        sa.Column("back", sa.Text(), nullable=False),
        sa.Column("kept_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rung", sa.Integer(), nullable=True),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("source_lesson_id", sa.Uuid(), nullable=True),
        sa.Column("source_path_id", sa.Uuid(), nullable=True),
        sa.Column("source_lesson_title", sa.Text(), nullable=False),
        sa.Column("source_path_title", sa.Text(), nullable=False),
        sa.Column("source_generated_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_lesson_id"], ["lessons.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["source_path_id"], ["paths.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_flashcards_user_id_due_on",
        "flashcards",
        ["user_id", "due_on"],
        postgresql_where=sa.text("kept_at IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "ix_flashcards_source_lesson_id", "flashcards", ["source_lesson_id"]
    )
    op.create_index(
        "ix_flashcards_user_id_kept_at",
        "flashcards",
        ["user_id", sa.text("kept_at DESC")],
        postgresql_where=sa.text("kept_at IS NOT NULL AND deleted_at IS NULL"),
    )

    op.create_table(
        "flashcard_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("card_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("grade", flashcard_grade, nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("local_day", sa.Date(), nullable=False),
        sa.Column("rung_before", sa.Integer(), nullable=False),
        sa.Column("rung_after", sa.Integer(), nullable=False),
        sa.Column("due_on_before", sa.Date(), nullable=False),
        sa.Column("due_on_after", sa.Date(), nullable=False),
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
        sa.ForeignKeyConstraint(["card_id"], ["flashcards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_flashcard_reviews_card_id_reviewed_at",
        "flashcard_reviews",
        ["card_id", "reviewed_at"],
    )
    op.create_index(
        "ix_flashcard_reviews_user_id_local_day",
        "flashcard_reviews",
        ["user_id", "local_day"],
    )
    op.create_index(
        "ix_flashcard_reviews_user_id_reviewed_at",
        "flashcard_reviews",
        ["user_id", "reviewed_at"],
    )

    op.create_table(
        "flashcard_draft_runs",
        sa.Column("lesson_id", sa.Uuid(), nullable=False),
        sa.Column("state", flashcard_draft_run_state, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("lesson_id"),
    )

    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "auto_draft_flashcards",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
