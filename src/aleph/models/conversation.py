"""Conversation ORM model (CONTEXT.md: the persisted thread, one per path)."""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aleph.db import Base
from aleph.models.base import UUIDAuditMixin
from aleph.models.enums import ConversationKind

if TYPE_CHECKING:
    from aleph.models.message import Message
    from aleph.models.path import Path


class Conversation(Base, UUIDAuditMixin):
    """The tutor's persisted thread for one path, of one **kind**.

    **One per path per kind** is a database constraint, not a convention
    (``uq_conversations_path_kind``): the row is created lazily, in the same
    transaction as the first completed turn (Phase 2 TDD §4/D2), and a second
    insert for the same ``(path, kind)`` fails loudly rather than forking the
    thread. Phase 2B widened that constraint from ``UNIQUE (path_id)`` (D3) so a
    path can carry both its in-lesson thread and its **Shaping conversation** —
    two threads, never two of either.

    The row carries no state, no status and no generation columns on purpose: a
    tutor reply is request-scoped, so there is nothing to recover and nothing to
    reconcile (TDD §4, contrast Phase 1 D5). "New conversation" is a ``DELETE``
    of this row — the cascade removes its messages — and deleting the path
    cascades through here with no extra code. Clearing a thread does **not**
    take the path's Change history with it: ``path_changes`` hangs off the path
    and only nulls its message reference (Phase 2B D3).
    """

    __tablename__ = "conversations"
    # Named explicitly so the model's constraint matches the migration's
    # ``uq_conversations_path_kind``; a column-level ``unique=True`` would
    # autogenerate ``conversations_path_id_key`` instead, silently diverging
    # from the migration.
    __table_args__ = (
        UniqueConstraint("path_id", "kind", name="uq_conversations_path_kind"),
    )

    path_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("paths.id", ondelete="CASCADE"),
        nullable=False,
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
