"""Card management: soft delete, edit provenance, and the list's own index.

AL-410 (issue #156) is the backend half of "Card list: browse, edit and delete
every kept card" — a post-phase amendment to Phase 3 (the flashcards flag is
still dark; no learner data exists yet, so this schema is free to move). Two
nullable columns on ``flashcards`` plus one rewritten index and one new one:

* ``deleted_at`` — **soft delete**. ``flashcard_reviews`` is ``ON DELETE
  CASCADE`` from ``flashcards`` (migration ``0010``), so a *hard* delete would
  erase the card's review log along with it. That log is not decoration: the
  Daily streak's union reads it (Phase 3 TDD D11, ``review_days_for_user``),
  so erasing it **retroactively removes past Active days from the streak**,
  skews recall-rate-by-rung (§9), and breaks D1's rebuildable-projection
  guarantee (the promise that ``rung``/``due_on`` can always be recomputed by
  replaying the log). A hard delete would have to accept all three costs, or
  invent a second place to record "this card no longer counts, but its history
  still does." One nullable column is cheaper than either. ``deleted_at IS NOT
  NULL`` means "gone from every learner-facing read" — the daily queue, the
  summary, the card list, grading — and nothing else changes: the row and its
  reviews both stay exactly where they are.

* ``edited_at`` — **edit provenance**. Phase 3 TDD D6 put Drafts and Kept
  cards in one table partly so the ``flashcard_draft`` eval artifact samples
  *what the agent actually produced*. Once a learner can rewrite a card's
  text (this ticket), a kept card sits at the trust boundary between
  agent-written and learner-written content, and ``edited_at`` is what keeps
  eval sampling able to tell the two apart (``edited_at IS NOT NULL`` excludes
  a learner-edited card from that sample, per ``docs/evals.md``).

* ``ix_flashcards_user_id_due_on`` — **rewritten**, not left alone. Its
  partial predicate widens from ``kept_at IS NOT NULL`` to ``kept_at IS NOT
  NULL AND deleted_at IS NULL``: the daily selection's hot path (Phase 3 §4
  item 1) must never serve a soft-deleted card, and a partial index whose
  predicate excludes fewer rows than the query's own ``WHERE`` clause is worse
  than no partial index at all — the planner can no longer trust it to already
  exclude what the query excludes. Missing this step would leave deleted
  cards sitting in the one index the queue read actually uses.

* ``ix_flashcards_user_id_kept_at`` — **new**, the card list's own ordering
  (``user_id, kept_at DESC``, most-recently-kept first), the same partial
  predicate as the rewritten index above — a deleted card must not linger in
  this index either.

Written in the ``0009``/``0010`` style: ``op.add_column``/``op.drop_column``
for the two columns, ``op.drop_index``/``op.create_index`` for the rewrite (an
index has no ``ALTER`` — dropping and recreating under the same name is the
only way to change its predicate), plain ``op.create_index`` for the new one.
``downgrade()`` reverses all four steps, in reverse order, ending with the
schema exactly as ``0010`` left it — including restoring the narrower
``kept_at IS NOT NULL`` predicate on ``ix_flashcards_user_id_due_on``.

Revision ID: 0011_flashcard_management
Revises: 0010_flashcards
Create Date: 2026-08-04
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0011_flashcard_management"
down_revision: str | None = "0010_flashcards"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

DUE_ON_INDEX = "ix_flashcards_user_id_due_on"
KEPT_AT_INDEX = "ix_flashcards_user_id_kept_at"


def upgrade() -> None:
    op.add_column(
        "flashcards", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "flashcards", sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True)
    )

    # Rewrite, not alter: excludes soft-deleted cards from the hot path (§4
    # item 1) — miss this and a deleted card stays in the one index the daily
    # selection actually uses.
    op.drop_index(DUE_ON_INDEX, table_name="flashcards")
    op.create_index(
        DUE_ON_INDEX,
        "flashcards",
        ["user_id", "due_on"],
        postgresql_where=sa.text("kept_at IS NOT NULL AND deleted_at IS NULL"),
    )

    # New: the card list's own ordering, same predicate as above.
    op.create_index(
        KEPT_AT_INDEX,
        "flashcards",
        ["user_id", sa.text("kept_at DESC")],
        postgresql_where=sa.text("kept_at IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(KEPT_AT_INDEX, table_name="flashcards")

    op.drop_index(DUE_ON_INDEX, table_name="flashcards")
    op.create_index(
        DUE_ON_INDEX,
        "flashcards",
        ["user_id", "due_on"],
        postgresql_where=sa.text("kept_at IS NOT NULL"),
    )

    op.drop_column("flashcards", "edited_at")
    op.drop_column("flashcards", "deleted_at")
