"""Path ORM model (CONTEXT.md: Path — a learning journey for one topic/level)."""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from datetime import (
    datetime,  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
)
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aleph.db import Base
from aleph.models.base import UUIDAuditMixin
from aleph.models.enums import Level, PathStatus

if TYPE_CHECKING:
    from aleph.models.lesson import Lesson
    from aleph.models.unit import Unit
    from aleph.models.users import User


class Path(Base, UUIDAuditMixin):
    """A structured learning journey for one topic at one level.

    An ordered set of units. ``status`` tracks outline generation
    (pending -> generating -> ready, with failed/refused branches, TDD §4).
    """

    __tablename__ = "paths"
    __table_args__ = (Index("ix_paths_user_id", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[Level] = mapped_column(
        Enum(
            Level,
            name="level",
            values_callable=lambda enum: [e.value for e in enum],
        ),
        nullable=False,
    )
    status: Mapped[PathStatus] = mapped_column(
        Enum(
            PathStatus,
            name="path_status",
            values_callable=lambda enum: [e.value for e in enum],
        ),
        nullable=False,
        default=PathStatus.PENDING,
    )
    refusal_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Admin model-picker overrides (AL-052, TDD §5.3/D14). An admin may select the
    # outline/lesson model per-path from ``MODEL_ALLOWLIST``; the choice is stored
    # here — not held on the request — so the DB-driven resume/reconcile
    # (§5.4/D6) re-generates with the chosen model rather than the config default.
    # ``NULL`` means "use the configured slot" (the default for every non-admin
    # and un-overridden create). Enforcement (admin-only, allowlist-bound) lives
    # at the create route; this column trusts an already-validated id.
    model_outline: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_lesson: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(back_populates="paths")
    units: Mapped[list[Unit]] = relationship(
        back_populates="path",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Unit.position",
    )
    lessons: Mapped[list[Lesson]] = relationship(
        back_populates="path",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Lesson.position_in_path",
    )
