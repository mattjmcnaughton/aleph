"""Per-user feature-flag override ORM model (AL-203)."""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from datetime import (
    datetime,  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
)

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from aleph.db import Base


class UserFeatureOverride(Base):
    """A per-user exception to a feature flag's default.

    Flags themselves are defined in code (:mod:`aleph.services.feature_flags`);
    only the exceptions live in the database. A missing row means "use the
    default", and rows for flags that no longer exist in code are ignored at
    resolution time, so deleting a flag never requires a data migration.

    Deliberately **not** a :class:`~aleph.models.base.UUIDAuditMixin` table: the
    natural key ``(user_id, flag_key)`` is the primary key, which is what makes
    the admin upsert a plain ``ON CONFLICT`` and caps the table at one row per
    user per flag. A surrogate id would allow two contradictory rows for the
    same pair. ``created_at`` is not carried either — an override is a current
    setting, not an event log.

    Ported from habagou's model of the same name.
    """

    __tablename__ = "user_feature_overrides"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Plain text, not an enum: adding or removing a flag in code must never be a
    # schema change (the registry is the only source of truth for which keys
    # exist, and unknown keys are ignored at resolution time).
    flag_key: Mapped[str] = mapped_column(String, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Server-side only: the sole write path is the repository's ``ON CONFLICT``
    # upsert, which sets ``updated_at`` in SQL. There is no ORM update path to
    # hang an ``onupdate`` off, and one would only be a second, quieter answer to
    # the same question.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
