"""Quick check ORM model (CONTEXT.md: the single-select MCQ ending a lesson)."""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aleph.db import Base
from aleph.models.base import UUIDAuditMixin

if TYPE_CHECKING:
    from aleph.models.attempt import Attempt
    from aleph.models.lesson import Lesson


class QuickCheck(Base, UUIDAuditMixin):
    """The single-select MCQ that ends a lesson.

    One per lesson (1:1, enforced by the unique ``lesson_id``). Composed of a
    stem, 3-4 options, one correct option (``correct_index``), and an
    explanation.
    """

    __tablename__ = "quick_checks"
    # Named explicitly so the model's constraint matches the migration's
    # ``uq_quick_checks_lesson``; a column-level ``unique=True`` would autogenerate
    # ``quick_checks_lesson_id_key`` instead, silently diverging from the migration.
    __table_args__ = (UniqueConstraint("lesson_id", name="uq_quick_checks_lesson"),)

    lesson_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("lessons.id", ondelete="CASCADE"),
        nullable=False,
    )
    stem: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    correct_index: Mapped[int] = mapped_column(Integer, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)

    lesson: Mapped[Lesson] = relationship(back_populates="quick_check")
    attempts: Mapped[list[Attempt]] = relationship(
        back_populates="quick_check",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
