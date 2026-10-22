"""Persisted tutor threads: one per lesson, or one shaping thread per path."""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Enum, ForeignKey, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aleph.db import Base
from aleph.models.base import UUIDAuditMixin
from aleph.models.enums import ConversationKind

if TYPE_CHECKING:
    from aleph.models.message import Message
    from aleph.models.path import Path


class Conversation(Base, UUIDAuditMixin):
    """A lesson's tutor thread or a path's **Shaping conversation**.

    The database enforces one conversation per scope. Lesson threads require
    a lesson; shaping threads have none. Rows are created with the first turn.

    The row carries no state, no status and no generation columns on purpose: a
    tutor reply is request-scoped, so there is nothing to recover and nothing to
    reconcile (TDD §4, contrast Phase 1 D5). "New conversation" is a ``DELETE``
    of this row — the cascade removes its messages — and deleting the path
    cascades through here with no extra code. Clearing a thread does **not**
    take the path's Change history with it: ``path_changes`` hangs off the path
    and only nulls its message reference (Phase 2B D3).
    """

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint(
            "path_id",
            "kind",
            "lesson_id",
            name="uq_conversations_scope",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "(kind = 'lesson' AND lesson_id IS NOT NULL) OR "
            "(kind = 'shaping' AND lesson_id IS NULL)",
            name="ck_conversations_scope",
        ),
    )

    path_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("paths.id", ondelete="CASCADE"),
        nullable=False,
    )
    lesson_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("lessons.id", ondelete="CASCADE"),
        nullable=True,
    )
    # Defaulted in the database as well as in Python: the 2B migration adds the
    # column with ``DEFAULT 'lesson'``, which is what backfills every existing
    # 2A row before the unique constraint is swapped (D3/§12).
    kind: Mapped[ConversationKind] = mapped_column(
        Enum(
            ConversationKind,
            name="conversation_kind",
            values_callable=lambda enum: [e.value for e in enum],
        ),
        nullable=False,
        default=ConversationKind.LESSON,
        server_default=ConversationKind.LESSON.value,
    )

    path: Mapped[Path] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.position",
    )
