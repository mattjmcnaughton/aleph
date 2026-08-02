"""Lesson ORM model (CONTEXT.md: Lesson — one Read passage + one Quick check)."""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from datetime import (
    datetime,  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
)
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aleph.db import Base
from aleph.models.base import UUIDAuditMixin
from aleph.models.enums import LessonGenerationState

if TYPE_CHECKING:
    from aleph.models.path import Path
    from aleph.models.quick_check import QuickCheck
    from aleph.models.unit import Unit


class Lesson(Base, UUIDAuditMixin):
    """The atomic unit of learning: one Read passage followed by one Quick check.

    ``position_in_path`` is the total order continuity and prefetch operate on
    (TDD §4); ``position_in_unit`` is for display. ``generation_state`` tracks
    on-demand content generation; ``completed_at`` drives derived unlock state.
    ``path_id`` is denormalized off ``unit`` so the total order can be queried
    without a join.
    """

    __tablename__ = "lessons"
    __table_args__ = (
        UniqueConstraint(
            "path_id",
            "position_in_path",
            name="uq_lessons_path_position_in_path",
        ),
        Index("ix_lessons_unit_id", "unit_id"),
        # The Streaks slice's one query (Phase 5 TDD D6, migration ``0009``):
        # ``completion_days_for_user`` joins on ``path_id`` and groups by the
        # local day derived from ``completed_at``, for exactly the completed
        # rows. Partial — most lessons on a growing path are incomplete, and
        # the query never wants those — so an index-only scan is available
        # covering both the join and the group-by without touching the row.
        Index(
            "ix_lessons_path_id_completed_at",
            "path_id",
            "completed_at",
            postgresql_where=text("completed_at IS NOT NULL"),
        ),
    )

    unit_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("units.id", ondelete="CASCADE"),
        nullable=False,
    )
    path_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("paths.id", ondelete="CASCADE"),
        nullable=False,
    )
    position_in_path: Mapped[int] = mapped_column(Integer, nullable=False)
    position_in_unit: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    generation_state: Mapped[LessonGenerationState] = mapped_column(
        Enum(
            LessonGenerationState,
            name="lesson_generation_state",
            values_callable=lambda enum: [e.value for e in enum],
        ),
        nullable=False,
        default=LessonGenerationState.UNGENERATED,
    )
    generation_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    generation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    read_passage: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # A learner-applied **Revision**'s instruction (Phase 2B TDD D7): set by
    # ``apply_change`` alongside the reset to ``ungenerated``, read by the
    # lesson prompt's revision block, and cleared when the row reaches
    # ``generated`` again. ``NULL`` is every ordinary lesson — which is why the
    # Phase 1 pipeline needs no changes to carry it.
    #
    # This column is the whole of the invariant amendment's storage: content is
    # immutable once **engaged**, not once generated (CONTEXT.md / PRD §6), so
    # ``generated -> ungenerated`` exists but only inside ``apply_change`` for
    # an unengaged lesson (D2 guard).
    revision_instruction: Mapped[str | None] = mapped_column(Text, nullable=True)

    unit: Mapped[Unit] = relationship(back_populates="lessons")
    path: Mapped[Path] = relationship(back_populates="lessons")
    quick_check: Mapped[QuickCheck | None] = relationship(
        back_populates="lesson",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
