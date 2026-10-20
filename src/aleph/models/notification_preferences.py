"""Account-wide Notification choices; absence means defaults."""

import uuid  # noqa: TC003

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, SmallInteger, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from aleph.db import Base


class NotificationPreferences(Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        CheckConstraint(
            "reminder_hour BETWEEN 0 AND 23", name="ck_notification_reminder_hour"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    reminder_enabled: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false")
    )
    reminder_hour: Mapped[int] = mapped_column(SmallInteger, server_default=text("20"))
    path_ready_enabled: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true")
    )
    brief_published_enabled: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true")
    )
