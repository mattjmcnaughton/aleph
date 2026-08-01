"""PathChange ORM model (CONTEXT.md: **Change** — an applied edit)."""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from datetime import (
    datetime,  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
)
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aleph.db import Base
from aleph.models.base import UUIDAuditMixin
from aleph.models.enums import PathChangeKind, PathChangeStatus

if TYPE_CHECKING:
    from aleph.models.message import Message
    from aleph.models.path import Path


class PathChange(Base, UUIDAuditMixin):
    """An applied edit to a path's structure: the unit of history and **Undo**.

    A row exists only because **Apply** committed — the explicit learner tap
    that turns a **Proposal** into a Change (CONTEXT.md). There is no *pending*
    status and no row for a declined proposal, which is what makes "the only
    write path into path structure is Apply on a validated Proposal" a
    property of the schema rather than a convention.

    **Owned by the path, not by the conversation.** ``path_id`` cascades;
    ``message_id`` is ``ON DELETE SET NULL`` — deliberately *not* cascade
    (Phase 2B TDD D3). "New conversation" deletes messages, and the Change
    history must survive it (PRD §5.8): history belongs to the path. A change
    whose proposal message is gone simply has no card to point back at.

    ``payload`` is the applied operations **plus their inverses** — created
    lesson/unit ids for an **Addition**, the full pre-revision snapshot
    (``read_passage``, the Quick check row, title, ``generated_at``) for a
    **Revision** — so the row is self-sufficient for undo (D8) and undo needs no
    second source of truth.

    ``applied_at`` is when structure landed, not when generation finished: a
    Change is applied the moment the rows exist, and the added or revised
    lessons then generate through the untouched Phase 1 pipeline (PRD §5.7).
    ``undone_at`` stays ``NULL`` for a live change. Whether undo is still *open*
    is not stored either — it is the D2 engagement re-check, run at undo time
    (:mod:`aleph.domains.engagement`), because the learner can engage at any
    moment and a stored flag would be stale immediately.
    """

    __tablename__ = "path_changes"
    __table_args__ = (
        # The history read is "this path's changes, newest first", and the FK
        # cascade from ``paths`` scans this column.
        Index("ix_path_changes_path_id", "path_id"),
        # Scanned by the ``SET NULL`` when a thread is cleared.
        Index("ix_path_changes_message_id", "message_id"),
        # **A Proposal is applied at most once**, in the database rather than in
        # one process's lock (migration ``0007``). Partial, so the legal
        # ``apply → undo → apply`` sequence is untouched and the NULL
        # ``message_id`` of a Change whose thread was cleared (D3) never
        # collides — NULLs are not equal to one another.
        Index(
            "uq_path_changes_applied_message",
            "message_id",
            unique=True,
            postgresql_where=text("status = 'applied'"),
        ),
    )

    path_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("paths.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    kind: Mapped[PathChangeKind] = mapped_column(
        Enum(
            PathChangeKind,
            name="path_change_kind",
            values_callable=lambda enum: [e.value for e in enum],
        ),
        nullable=False,
    )
    # Reassign, never mutate in place (as ``Message.tutor_check``): plain JSONB
    # has no ORM mutation tracking, so an in-place edit is never flushed.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[PathChangeStatus] = mapped_column(
        Enum(
            PathChangeStatus,
            name="path_change_status",
            values_callable=lambda enum: [e.value for e in enum],
        ),
        nullable=False,
        default=PathChangeStatus.APPLIED,
        server_default=PathChangeStatus.APPLIED.value,
    )
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    undone_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    path: Mapped[Path] = relationship(back_populates="changes")
    # One-directional on purpose: there is no ``Message.changes`` to keep in
    # sync, so nothing in the ORM competes with the database's ``SET NULL`` when
    # a thread is cleared — the schema, not application code, is what makes
    # history survive (D3).
    message: Mapped[Message | None] = relationship()
