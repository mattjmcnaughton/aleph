"""One durable Reminder attempt per learner-local day, not a delivery receipt."""

import uuid  # noqa: TC003
from datetime import date, datetime  # noqa: TC003

from sqlalchemy import Date, DateTime, ForeignKey, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from aleph.db import Base


class ReminderSend(Base):
    __tablename__ = "reminder_sends"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    local_date: Mapped[date] = mapped_column(Date, primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    outcome: Mapped[str] = mapped_column(Text)
