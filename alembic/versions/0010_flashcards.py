"""The flashcards branch: cards, their review log, and a per-lesson draft-run row.

Creates the Phase 3 schema (TDD §4), the whole of D1's storage decision — **two**
new things, ``flashcards`` and ``flashcard_reviews``, plus the sparse
``flashcard_draft_runs`` claim row (D7):

* ``flashcards`` — one row per **Draft** (``kept_at IS NULL``) or **Kept card**.
  ``rung``/``due_on`` are the D1 projection over ``flashcard_reviews``, ``NULL``
  until a keep sets them. Both source FKs (``source_lesson_id``/
  ``source_path_id``) are ``ON DELETE SET NULL``, and both titles are copied at
  draft time (D12) so a card's citation survives its source. ``user_id`` is
  denormalized (§4 item 3) and cascades with the account.
* ``flashcard_reviews`` — append-only, the source of truth (D1): ``rung``/
  ``due_on`` before and after every grade, so the projection is rebuildable by
  replay. ``card_id``/``user_id`` cascade.
* ``flashcard_draft_runs`` — one sparse row per *drafted* lesson (D7),
  ``lesson_id`` primary key, cascading with its lesson.

The partial index ``ix_flashcards_user_id_due_on ... WHERE kept_at IS NOT NULL``
is the hot path (§4 item 1) — the Phase 5 D6 (``0009``) shape, excluding
drafts, which the daily queue read never wants. Written in the
``0005_shaping``/``0009_lesson_completed_at_index`` style: three
``op.create_table`` calls plus indexes, additive and reversible by dropping the
three tables (and the two enum types this step creates).

Revision ID: 0010_flashcards
Revises: 0009_lesson_completed_at_index
Create Date: 2026-08-04
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010_flashcards"
down_revision: str | None = "0009_lesson_completed_at_index"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

flashcard_grade = postgresql.ENUM(
    "again",
    "got_it",
    name="flashcard_grade",
    create_type=False,
)
flashcard_draft_run_state = postgresql.ENUM(
    "generating",
    "generated",
    "failed",
    name="flashcard_draft_run_state",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    flashcard_grade.create(bind, checkfirst=True)
    flashcard_draft_run_state.create(bind, checkfirst=True)

    op.create_table(
        "flashcards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("front", sa.Text(), nullable=False),
        sa.Column("back", sa.Text(), nullable=False),
        # NULL = a Draft (D6); set atomically by the keep transaction (§5.2).
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        # Both source FKs are SET NULL, never CASCADE (D12/§4 item 4): deleting
        # the source must not delete the learner's card.
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
        postgresql_where=sa.text("kept_at IS NOT NULL"),
    )
    op.create_index(
        "ix_flashcards_source_lesson_id", "flashcards", ["source_lesson_id"]
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
        # The primary key *is* the lesson id (D7): one sparse row per drafted
        # lesson, no surrogate id — the ``UserFeatureOverride`` shape.
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


def downgrade() -> None:
    op.drop_table("flashcard_draft_runs")

    op.drop_index(
        "ix_flashcard_reviews_user_id_reviewed_at", table_name="flashcard_reviews"
    )
    op.drop_index(
        "ix_flashcard_reviews_user_id_local_day", table_name="flashcard_reviews"
    )
    op.drop_index(
        "ix_flashcard_reviews_card_id_reviewed_at", table_name="flashcard_reviews"
    )
    op.drop_table("flashcard_reviews")

    op.drop_index("ix_flashcards_source_lesson_id", table_name="flashcards")
    op.drop_index("ix_flashcards_user_id_due_on", table_name="flashcards")
    op.drop_table("flashcards")

    bind = op.get_bind()
    flashcard_draft_run_state.drop(bind, checkfirst=True)
    flashcard_grade.drop(bind, checkfirst=True)
