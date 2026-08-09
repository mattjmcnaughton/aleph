"""Add ``beat_research_runs`` — the daily research cap's own counter.

Code-review FIX 2 on AL-521 (epic #163's correctness review), not part of the
original Phase 6 TDD schema (``0012_analyst``). The cap the TDD specifies
(``RATE_LIMIT_BRIEF_RESEARCH_PER_DAY``, D14/D14a) counted ``beats`` rows whose
``research_started_at`` fell today — but a claim *overwrites* that single
stamp on every (re-)claim, so the count could never exceed the learner's
*Beat* count (bounded at 3 by ``MAX_BEATS_PER_LEARNER``), which is always
below the cap (5). This migration adds one append-only row per WON research
claim so the cap counts RUNS, not Beats — see
``aleph.models.beat_research_run.BeatResearchRun`` for the full write-up,
including why this is **not** a revival of Phase 6 TDD D2a's rejected
``beat_runs`` table (that proposal carried run *outcomes* and would have
replaced the Beat rail read; this table carries neither and is read by
exactly one query, the cap's own ``COUNT``).

Purely additive: one new table, two new indexes, nothing touched on any
existing table. Online-safe on Neon at any size. Rollback is dropping the one
table nothing else references.

Revision ID: 0013_beat_research_runs
Revises: 0012_analyst
Create Date: 2026-08-09
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0013_beat_research_runs"
down_revision: str | None = "0012_analyst"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

USER_STARTED_AT_INDEX = "ix_beat_research_runs_user_id_started_at"
BEAT_ID_INDEX = "ix_beat_research_runs_beat_id"


def upgrade() -> None:
    op.create_table(
        "beat_research_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("beat_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # The count query's own index: WHERE user_id = ? AND started_at >= ?.
    op.create_index(
        USER_STARTED_AT_INDEX, "beat_research_runs", ["user_id", "started_at"]
    )
    # Supports the cascade delete's own lookup when a Beat is removed.
    op.create_index(BEAT_ID_INDEX, "beat_research_runs", ["beat_id"])


def downgrade() -> None:
    op.drop_index(BEAT_ID_INDEX, table_name="beat_research_runs")
    op.drop_index(USER_STARTED_AT_INDEX, table_name="beat_research_runs")
    op.drop_table("beat_research_runs")
