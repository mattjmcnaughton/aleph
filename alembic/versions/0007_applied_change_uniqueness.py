"""One **applied** Change per Proposal, enforced by the database (AL-321).

"A Proposal is applied at most once" is the phase's consent rule (TDD §5.6): the
learner's tap is the consent, and a Proposal that has already become structure
must not become structure a second time. Until this step that rule lived in two
in-process places — the per-path apply lock and the pre-check that reads the
history inside it — and both are scoped to **one process**. A Fly rolling deploy
briefly runs two machines, and two taps that land on different machines pass
each other cleanly: neither lock excludes the other, and both pre-checks read a
history that does not yet contain the other's row.

So the invariant moves where invariants belong. A **partial** unique index on
``path_changes(message_id) WHERE status = 'applied'`` says exactly the rule and
nothing more:

* ``message_id`` is nullable and NULLs are not equal to one another, so the
  Changes whose proposal message a "new conversation" cleared (D3 — history
  outlives the thread) are all outside the index and never collide;
* it is scoped to ``applied``, so the legal ``apply → undo → apply`` sequence
  for one message still works — an undone row leaves the index the moment it is
  undone. (Apply refuses that second tap for its own reason, ``already_undone``;
  that is a product rule and stays in the service, where it can be changed
  without a migration.)

The loser of a genuine cross-process race gets an ``IntegrityError`` on the
insert, which ``services/shaping.py`` maps to the same ``409 already_applied``
the in-process pre-check returns. Same answer, whichever guard caught it.

Additive and index-only: no column, no data, no constraint on an existing
column, and the downgrade drops just this index.

Revision ID: 0007_applied_change_uniqueness
Revises: 0006_shaping_message_lesson
Create Date: 2026-08-01
"""

from __future__ import annotations

from alembic import op

revision: str = "0007_applied_change_uniqueness"
down_revision: str | None = "0006_shaping_message_lesson"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

INDEX_NAME = "uq_path_changes_applied_message"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "path_changes",
        ["message_id"],
        unique=True,
        postgresql_where="status = 'applied'",
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="path_changes")
