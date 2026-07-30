"""Conversation ORM model (CONTEXT.md: the persisted thread, one per path)."""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aleph.db import Base
from aleph.models.base import UUIDAuditMixin

if TYPE_CHECKING:
    from aleph.models.message import Message
    from aleph.models.path import Path


class Conversation(Base, UUIDAuditMixin):
    """The tutor's persisted thread for one path.

    **One per path** is a database constraint, not a convention
    (``uq_conversations_path``): the row is created lazily, in the same
    transaction as the first completed turn (Phase 2 TDD §4/D2), and a second
    insert for the same path fails loudly rather than forking the thread.

    The row carries no state, no status and no generation columns on purpose: a
    tutor reply is request-scoped, so there is nothing to recover and nothing to
    reconcile (TDD §4, contrast Phase 1 D5). "New conversation" is a ``DELETE``
    of this row — the cascade removes its messages — and deleting the path
    cascades through here with no extra code.
    """

    __tablename__ = "conversations"
    # Named explicitly so the model's constraint matches the migration's
    # ``uq_conversations_path``; a column-level ``unique=True`` would autogenerate
    # ``conversations_path_id_key`` instead, silently diverging from the migration.
    __table_args__ = (UniqueConstraint("path_id", name="uq_conversations_path"),)

    path_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("paths.id", ondelete="CASCADE"),
        nullable=False,
    )

    path: Mapped[Path] = relationship(back_populates="conversation")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.position",
    )
