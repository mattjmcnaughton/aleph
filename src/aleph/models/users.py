"""Learner account ORM model (CONTEXT.md: Account / Learner)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aleph.db import Base
from aleph.models.base import UUIDAuditMixin

if TYPE_CHECKING:
    from aleph.models.attempt import Attempt
    from aleph.models.path import Path


class User(Base, UUIDAuditMixin):
    """A learner's authenticated account.

    Identity comes from the OIDC provider and is keyed on ``(issuer, subject)``
    (habagou identity model). Owns the learner's paths and attempts.

    ``email`` is nullable: OIDC providers do not guarantee an email claim, and
    identity is keyed on ``(issuer, subject)`` rather than email (habagou identity
    model). TDD §4 lists ``email`` without a NOT NULL annotation; it stays
    nullable deliberately.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),
    )

    issuer: Mapped[str] = mapped_column(String, nullable=False)
    subject: Mapped[str] = mapped_column(String, nullable=False)
    username: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str | None] = mapped_column(String, nullable=True)

    paths: Mapped[list[Path]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    attempts: Mapped[list[Attempt]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
