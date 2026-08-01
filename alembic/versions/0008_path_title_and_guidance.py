"""Path title and Guidance: two additive, nullable columns on ``paths``.

Adds `docs/CONTEXT.md`'s two newest terms to the schema:

* ``paths.title`` — the learner-editable **Path title** (CONTEXT.md), the
  *display* label shown in the switcher and the path view. It is deliberately
  **not** a generation input: no agent prompt reads it (structurally enforced —
  it is absent from every ``*Deps`` dataclass, `src/aleph/models/path.py`'s
  comment on ``topic`` explains why). ``NULL`` means "no rename yet, show the
  Topic" — the fallback the ORM's ``display_title`` property applies and the
  API always resolves before the value reaches the wire, so a reader never has
  to know the column can be empty.
* ``paths.guidance`` — the learner's optional free text captured **once, at
  creation** (CONTEXT.md: **Guidance**), steering the outline's shape alongside
  Topic and Level. Persisted on the row (not carried only in memory) for the
  same reason the admin model overrides are (0002): the DB-driven resume and
  reconciler (Phase 1 TDD §5.4/D6) must re-run the outline with the same
  guidance a crashed or restarted attempt saw, not a blank one.

Both are ``NULL``-able ``TEXT`` with **no backfill**: every row that exists
before this migration was created before either concept existed, so there is
no historical value to compute — ``NULL`` is the correct, honest state for
them (an untitled path with no guidance ever given), not a placeholder to fill
in. That is also why this migration needs no ``server_default`` and no
data-touching ``UPDATE``: it is schema-only, unlike 0005's backfilled
``kind`` column, which had a real "what were these before" answer.

Additive and column-only, so the downgrade is two plain ``drop_column`` calls
with no data-loss caveat beyond the columns' own contents.

Revision ID: 0008_path_title_and_guidance
Revises: 0007_applied_change_uniqueness
Create Date: 2026-08-01
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0008_path_title_and_guidance"
down_revision: str | None = "0007_applied_change_uniqueness"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column("paths", sa.Column("title", sa.Text(), nullable=True))
    op.add_column("paths", sa.Column("guidance", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("paths", "guidance")
    op.drop_column("paths", "title")
