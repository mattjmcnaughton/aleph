"""LessonVisual ORM model (CONTEXT.md: **Visual** — stored beside the Read passage)."""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Index, Integer, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aleph.db import Base
from aleph.models.base import UUIDAuditMixin
from aleph.models.enums import LessonVisualKind

if TYPE_CHECKING:
    from aleph.models.lesson import Lesson


class LessonVisual(Base, UUIDAuditMixin):
    """One interactive visual attached to a lesson's Read passage.

    The row's ``id`` **is** the visual's identifier: the passage references it
    with a ```visual <id> fence, rewritten from the agent's placeholder ref when
    the lesson is persisted (docs/tdds/lesson-visuals.md). ``source`` is the
    agent-written HTML body, run only inside the client's sandbox and wrapped in
    the kit version ``kit_version`` names; ``alt`` is the plain-text fallback.

    Written in the same transaction as the passage and Quick check, deleted with
    them by a **Revision**, and restored with its id by **Undo**.
    """

    __tablename__ = "lesson_visuals"
    __table_args__ = (Index("ix_lesson_visuals_lesson_id", "lesson_id"),)

    lesson_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("lessons.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[LessonVisualKind] = mapped_column(
        Enum(
            LessonVisualKind,
            name="lesson_visual_kind",
            values_callable=lambda enum: [e.value for e in enum],
        ),
        nullable=False,
        default=LessonVisualKind.INTERACTIVE,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    alt: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    kit_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    lesson: Mapped[Lesson] = relationship(back_populates="visuals")
