"""Attempt ORM model (CONTEXT.md: a learner answering a Quick check)."""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, Integer, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aleph.db import Base
from aleph.models.base import UUIDAuditMixin

if TYPE_CHECKING:
    from aleph.models.quick_check import QuickCheck
    from aleph.models.users import User


class Attempt(Base, UUIDAuditMixin):
    """A learner answering a Quick check: the option selected and whether correct.

    One Attempt per learner per Quick check (the first answer is the Outcome of
    record). ``user_id`` is denormalized off the quick check's path for metrics
    (TDD §4).
    """

    __tablename__ = "attempts"
    __table_args__ = (
        UniqueConstraint(
            "quick_check_id",
            "user_id",
            name="uq_attempts_quick_check_user",
        ),
        Index("ix_attempts_user_id", "user_id"),
    )

    quick_check_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("quick_checks.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    selected_index: Mapped[int] = mapped_column(Integer, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)

    quick_check: Mapped[QuickCheck] = relationship(back_populates="attempts")
    user: Mapped[User] = relationship(back_populates="attempts")
