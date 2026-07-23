"""Unit ORM model (CONTEXT.md: Unit — an ordered grouping of lessons)."""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aleph.db import Base
from aleph.models.base import UUIDAuditMixin

if TYPE_CHECKING:
    from aleph.models.lesson import Lesson
    from aleph.models.path import Path


class Unit(Base, UUIDAuditMixin):
    """An ordered grouping of lessons within a path.

    ``position`` orders units for display; it is unique within a path.
    """

    __tablename__ = "units"
    __table_args__ = (
        UniqueConstraint("path_id", "position", name="uq_units_path_position"),
    )

    path_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("paths.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    path: Mapped[Path] = relationship(back_populates="units")
    lessons: Mapped[list[Lesson]] = relationship(
        back_populates="unit",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Lesson.position_in_unit",
    )
