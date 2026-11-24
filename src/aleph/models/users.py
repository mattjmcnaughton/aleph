"""Learner account ORM model (CONTEXT.md: Account / Learner)."""

from __future__ import annotations

import datetime  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    String,
    Text,
    UniqueConstraint,
    false,
    true,
)
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
        UniqueConstraint("handle", name="uq_users_handle"),
        CheckConstraint("handle ~ '^[a-z0-9-]{3,30}$'", name="ck_users_handle_format"),
    )

    issuer: Mapped[str] = mapped_column(String, nullable=False)
    subject: Mapped[str] = mapped_column(String, nullable=False)
    username: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    time_zone: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_zone_seen_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # CONTEXT.md: *About you*. Read by every outline, lesson, tutor and shaping
    # prompt when it runs, so an edit is forward-only, like Teaching style.
    about_you: Mapped[str | None] = mapped_column(Text, nullable=True)
    # CONTEXT.md: *Handle* and *Public profile* (0025). The handle is written
    # once and never released (Public profiles TDD D8); ``domains/
    # public_profiles.py`` owns its rules, the CHECK is the storage backstop.
    handle: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    profile_show_activity: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

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
