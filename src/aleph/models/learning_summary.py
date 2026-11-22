"""One stored Learning summary per account (CONTEXT.md: *Learning summary*)."""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from datetime import datetime  # noqa: TC003
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aleph.db import Base


class LearningSummary(Base):
    """The last written summary and the state of the run writing the next.

    ``body`` survives a refresh: while a new run is ``generating`` (or after it
    ``failed``) the previous summary is still the one the learner reads.
    ``started_at`` fences completion, so only the run that claimed the row can
    settle it.
    """

    __tablename__ = "learning_summaries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('generating', 'ready', 'failed')",
            name="ck_learning_summaries_status",
        ),
        CheckConstraint(
            "(body IS NULL) = (written_at IS NULL)",
            name="ck_learning_summaries_body_written",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    written_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
