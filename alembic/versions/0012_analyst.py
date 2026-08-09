"""The analyst branch: Beats, Briefs (published or Skipped), and their Sources.

AL-511 (issue #168, part of epic #163) creates Phase 6's whole schema (TDD §4)
— **three** new tables, **two** new enums (``level`` is reused, not
redeclared), additive throughout: nothing on an existing table, so nothing
here is online-risky on Neon.

* ``beats`` — one row per standing research assignment (CONTEXT.md: Beat).
  Carries its own claim protocol, the ``paths`` precedent (TDD D3):
  ``research_state`` (idle -> researching -> idle, with failed/refused
  branches), ``research_started_at`` (the claim fence), ``research_error``,
  ``refusal_message``. ``anchor_weekday`` is bounded 0..6 (Python's
  Monday == 0) by ``ck_beats_anchor_weekday_range``.
* ``briefs`` — one row per dated entry: a numbered, titled, bodied
  **published** report, or an unnumbered, one-line **Skipped** period (D2).
  Two ``CHECK`` constraints (``ck_briefs_published_shape``,
  ``ck_briefs_skipped_shape``) make the discriminated shape structural, not
  conventional — a padded Brief cannot be written as Skipped and a Skipped
  period cannot acquire a body, at the storage layer. ``number`` is sparse
  (``NULL`` on Skipped rows) under the **partial** unique index
  ``uq_briefs_beat_id_number ... WHERE number IS NOT NULL``, which is what
  lets any number of Skipped rows share one Beat: a partial index simply does
  not index the NULLs, so the UNIQUE never sees two of them to collide.
  ``claims TEXT[]`` is a Postgres array (D9's whole-Beat read, never queried
  element-wise), not a table.
* ``brief_sources`` — one row per cited document (CONTEXT.md: Source), a
  table rather than a JSONB column (D1 amendment) because D9's novelty gate
  reads prior cited Source URLs across a Beat's *whole history* — a hot,
  structured read a JSONB column would make an unnest.

**Three things TDD §4 names as deliberately absent, each because it is
derivable** (Phase 5 D1's grain): no ``next_claimable_at`` on ``beats``
(``domains/cadence.py`` derives it from ``max(published_on)`` and
``anchor_weekday``); no ``builds_on_brief_id`` on ``briefs`` ("Builds on
Brief #4" is a ``WHERE number < :n ORDER BY number DESC LIMIT 1`` read); no
``title`` on ``beats`` (the Topic is the label).

Written in the ``0010_flashcards`` style: three ``op.create_table`` calls
plus indexes and check constraints, additive and reversible by dropping the
three tables (and the two enum types this step creates) in reverse dependency
order.

Revision ID: 0012_analyst
Revises: 0011_flashcard_management
Create Date: 2026-08-09
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_analyst"
down_revision: str | None = "0011_flashcard_management"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

BEAT_USER_ID_INDEX = "ix_beats_user_id"
BRIEF_NUMBER_INDEX = "uq_briefs_beat_id_number"
BRIEF_PUBLISHED_ON_INDEX = "ix_briefs_beat_id_published_on"
BRIEF_SOURCE_INDEX = "ix_brief_sources_brief_id"

beat_research_state = postgresql.ENUM(
    "idle",
    "researching",
    "failed",
    "refused",
    name="beat_research_state",
    create_type=False,
)
brief_kind = postgresql.ENUM(
    "published",
    "skipped",
    name="brief_kind",
    create_type=False,
)
# ``level`` is the existing enum type (migration 0001), reused verbatim
# (TDD D13's correction: "two enums", not four) — ``create_type=False`` here
# too, since this step must never attempt to (re)create it.
level_enum = postgresql.ENUM(
    "new_to_it",
    "some_experience",
    "work_in_it",
    name="level",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    beat_research_state.create(bind, checkfirst=True)
    brief_kind.create(bind, checkfirst=True)

    op.create_table(
        "beats",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("guidance", sa.Text(), nullable=True),
        sa.Column("level", level_enum, nullable=False),
        sa.Column("anchor_weekday", sa.SmallInteger(), nullable=False),
        sa.Column(
            "research_state",
            beat_research_state,
            nullable=False,
            server_default="idle",
        ),
        sa.Column("research_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("research_error", sa.Text(), nullable=True),
        sa.Column("refusal_message", sa.Text(), nullable=True),
        sa.Column("model_research", sa.Text(), nullable=True),
        sa.Column("model_brief", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "anchor_weekday >= 0 AND anchor_weekday <= 6",
            name="ck_beats_anchor_weekday_range",
        ),
    )
    op.create_index(BEAT_USER_ID_INDEX, "beats", ["user_id"])

    op.create_table(
        "briefs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("beat_id", sa.Uuid(), nullable=False),
        sa.Column("kind", brief_kind, nullable=False),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_on", sa.Date(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("body_markdown", sa.Text(), nullable=True),
        sa.Column("skip_line", sa.Text(), nullable=True),
        sa.Column(
            "claims",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sources_seen_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["beat_id"], ["beats.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # D2, made structural: a padded Brief cannot be written as Skipped and
        # a Skipped period cannot acquire a body, whatever any service does.
        sa.CheckConstraint(
            "kind <> 'published' OR (number IS NOT NULL AND title IS NOT NULL "
            "AND body_markdown IS NOT NULL AND skip_line IS NULL)",
            name="ck_briefs_published_shape",
        ),
        sa.CheckConstraint(
            "kind <> 'skipped' OR (number IS NULL AND body_markdown IS NULL "
            "AND skip_line IS NOT NULL)",
            name="ck_briefs_skipped_shape",
        ),
    )
    # Partial: sparse over published Briefs only (D2) — any number of Skipped
    # rows (NULL number) may share a Beat, since a partial index never indexes
    # the NULLs the UNIQUE would otherwise collide on.
    op.create_index(
        BRIEF_NUMBER_INDEX,
        "briefs",
        ["beat_id", "number"],
        unique=True,
        postgresql_where=sa.text("number IS NOT NULL"),
    )
    # The rail read: WHERE beat_id = ? ORDER BY published_on DESC, both kinds
    # interleaved.
    op.create_index(
        BRIEF_PUBLISHED_ON_INDEX,
        "briefs",
        ["beat_id", sa.text("published_on DESC")],
    )

    op.create_table(
        "brief_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("brief_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("publisher", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("published_on", sa.Date(), nullable=False),
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
        sa.ForeignKeyConstraint(["brief_id"], ["briefs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "brief_id", "position", name="uq_brief_sources_brief_id_position"
        ),
    )
    op.create_index(BRIEF_SOURCE_INDEX, "brief_sources", ["brief_id"])


def downgrade() -> None:
    op.drop_index(BRIEF_SOURCE_INDEX, table_name="brief_sources")
    op.drop_table("brief_sources")

    op.drop_index(BRIEF_PUBLISHED_ON_INDEX, table_name="briefs")
    op.drop_index(BRIEF_NUMBER_INDEX, table_name="briefs")
    op.drop_table("briefs")

    op.drop_index(BEAT_USER_ID_INDEX, table_name="beats")
    op.drop_table("beats")

    bind = op.get_bind()
    brief_kind.drop(bind, checkfirst=True)
    beat_research_state.drop(bind, checkfirst=True)
