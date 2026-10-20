"""One durable scheduled Beat scan frontier, not execution history."""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.

from sqlalchemy import Boolean, CheckConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from aleph.db import Base


class BeatScanProgress(Base):
    """Checkpoint admission even when eligibility checks or research time out.

    The frontier is not a foreign key: deleting a Beat must not reset progress.
    No claims, outcomes, timestamps or per-invocation rows live here.
    """

    __tablename__ = "beat_scan_progress"
    __table_args__ = (CheckConstraint("singleton", name="ck_beat_scan_singleton"),)

    singleton: Mapped[bool] = mapped_column(Boolean, primary_key=True)
    after: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
