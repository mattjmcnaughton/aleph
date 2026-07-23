"""Shared column mixin for the ORM models.

Every Aleph table carries a UUID primary key plus ``created_at`` / ``updated_at``
timestamps (TDD §4). Defining them once here keeps that invariant provably
consistent across every model.
"""

from __future__ import annotations

import uuid
from datetime import (
    datetime,  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
)

from sqlalchemy import DateTime, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column


class UUIDAuditMixin:
    """UUID primary key and audit timestamps shared by every table.

    ``updated_at`` gets a server default at insert and is bumped via SQLAlchemy
    ``onupdate`` on every ORM flush. All external references use the UUID; no
    serial ids, no slugs (TDD §4).
    """

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
