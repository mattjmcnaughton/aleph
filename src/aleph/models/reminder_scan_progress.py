"""One durable Reminder scan frontier, independent of daily send claims."""

import uuid  # noqa: TC003

from sqlalchemy import Boolean, CheckConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from aleph.db import Base


class ReminderScanProgress(Base):
    __tablename__ = "reminder_scan_progress"
    __table_args__ = (CheckConstraint("singleton", name="ck_reminder_scan_singleton"),)

    singleton: Mapped[bool] = mapped_column(Boolean, primary_key=True)
    # Not a foreign key: deleting an account must not reset the scan.
    after: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
