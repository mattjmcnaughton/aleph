"""Shaping messages are path-level: ``messages.lesson_id`` becomes NULLABLE.

The one schema gap Phase 2B TDD §4 did not spell out, and it is load-bearing for
AL-320. §4 lists ``conversations.kind``, ``messages.proposal``,
``lessons.revision_instruction`` and ``path_changes`` — every addition a shaping
thread needs *except* the column it cannot fill. ``messages.lesson_id`` is
``NOT NULL`` from the 2A era (``0003``), where it was exactly right: an in-lesson
turn is always asked **in** a lesson, and the thread renders lesson dividers
from it. A **Shaping conversation** is about the path as a whole (PRD §5.1) —
there is no lesson the turn was asked in, and inventing one (the first lesson?
the one the rail happened to be over?) would put a false fact in the record and
in every later read of it.

So the column becomes nullable and shaping rows store ``NULL``. Recorded here
rather than folded quietly into ``0005``: this is an extension of §4 by ruling,
not an improvisation, and a reader comparing the TDD's table to the schema
should find the difference explained at the point it was made.

**Upgrade** drops the ``NOT NULL`` and nothing else — no data moves, no
constraint is swapped, no index changes. Every 2A row keeps its real
``lesson_id`` and every 2A read of it is bit-identical (W21); the only rows that
can hold ``NULL`` are ones this migration makes possible.

**Downgrade** restores ``NOT NULL``, which requires there to be no ``NULL``
left. It deletes those rows first — shaping messages, exactly the rows that
could not exist before Phase 2B, the same data-loss-on-downgrade posture
``0005`` takes for shaping *conversations* (§12). The two steps compose: this
one empties the shaping threads, ``0005``'s reversal then removes the threads
themselves. A partial turn is never left behind, because *every* message in a
shaping thread has a ``NULL`` ``lesson_id`` — the delete takes whole threads'
worth or nothing.

Revision ID: 0006_shaping_message_lesson
Revises: 0005_shaping
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0006_shaping_message_lesson"
down_revision: str | None = "0005_shaping"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.alter_column(
        "messages",
        "lesson_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )


def downgrade() -> None:
    # ``NOT NULL`` cannot be restored while a shaping message exists, and a
    # shaping message is *defined* by having no lesson (see the module
    # docstring). Dropping them is the reversal; no pre-2B row can be caught by
    # it, because no pre-2B row was allowed to be NULL.
    op.execute("DELETE FROM messages WHERE lesson_id IS NULL")
    op.alter_column(
        "messages",
        "lesson_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
