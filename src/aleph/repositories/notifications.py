"""Notification persistence; caller owns transactions and delivery policy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Date, cast, delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert

from aleph.models import (
    Beat,
    Brief,
    BriefKind,
    Device,
    Lesson,
    NotificationPreferences,
    Path,
    ReminderScanProgress,
    ReminderSend,
    User,
)

if TYPE_CHECKING:
    import datetime
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.dtos.notifications import (
        NotificationPreferencesDTO,
        RegisterDeviceRequest,
    )


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def reminder_frontier(self) -> uuid.UUID | None:
        return await self.session.scalar(select(ReminderScanProgress.after))

    async def checkpoint_reminder_scan(self, after: uuid.UUID | None) -> None:
        await self.session.execute(update(ReminderScanProgress).values(after=after))

    async def reminder_accounts(
        self, after: uuid.UUID | None, *, until: uuid.UUID | None = None
    ) -> list[uuid.UUID]:
        query = (
            select(User.id)
            .join(NotificationPreferences, NotificationPreferences.user_id == User.id)
            .where(
                NotificationPreferences.reminder_enabled.is_(True),
                User.time_zone.is_not(None),
                exists(select(Device.id).where(Device.user_id == User.id)),
            )
            .order_by(User.id)
            .limit(100)
        )
        if after is not None:
            query = query.where(User.id > after)
        if until is not None:
            query = query.where(User.id <= until)
        return list((await self.session.scalars(query)).all())

    async def completion_dates(
        self, user_id: uuid.UUID, zone: str
    ) -> set[datetime.date]:
        # Apply IANA rules at EACH instant, including historical DST offsets.
        day = cast(func.timezone(zone, Lesson.completed_at), Date)
        return set(
            (
                await self.session.scalars(
                    select(day)
                    .join(Path, Path.id == Lesson.path_id)
                    .where(Path.user_id == user_id, Lesson.completed_at.is_not(None))
                    .distinct()
                )
            ).all()
        )

    async def claim_reminder(
        self, user_id: uuid.UUID, today: datetime.date, now: datetime.datetime
    ) -> bool:
        result = await self.session.scalar(
            insert(ReminderSend)
            .values(user_id=user_id, local_date=today, sent_at=now, outcome="attempted")
            .on_conflict_do_nothing()
            .returning(ReminderSend.user_id)
        )
        return result is not None

    async def reminder_outcome(
        self, user_id: uuid.UUID, today: datetime.date, outcome: str
    ) -> None:
        await self.session.execute(
            update(ReminderSend)
            .where(ReminderSend.user_id == user_id, ReminderSend.local_date == today)
            .values(outcome=outcome)
        )

    async def published_brief(self, brief_id: uuid.UUID) -> tuple[Brief, Beat] | None:
        row = (
            await self.session.execute(
                select(Brief, Beat)
                .join(Beat, Beat.id == Brief.beat_id)
                .where(Brief.id == brief_id, Brief.kind == BriefKind.PUBLISHED)
            )
        ).first()
        return (row[0], row[1]) if row else None

    async def register(
        self, user_id: uuid.UUID, body: RegisterDeviceRequest, user_agent: str | None
    ) -> uuid.UUID:
        values = dict(
            user_id=user_id,
            p256dh=body.keys.p256dh,
            auth=body.keys.auth,
            user_agent=user_agent,
            last_registered_at=func.clock_timestamp(),
        )
        statement = insert(Device).values(endpoint=body.endpoint, **values)
        return (
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=[Device.endpoint], set_=values
                ).returning(Device.id)
            )
        ).scalar_one()

    async def get_device(self, device_id: uuid.UUID) -> Device | None:
        return await self.session.get(Device, device_id)

    async def delete_device(self, device_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self.session.execute(
            delete(Device).where(Device.id == device_id, Device.user_id == user_id)
        )

    async def remove_gone(self, device: Device) -> bool:
        # Never remove a refreshed/reassigned subscription on a stale 410.
        result = await self.session.execute(
            delete(Device)
            .where(
                Device.id == device.id,
                Device.user_id == device.user_id,
                Device.endpoint == device.endpoint,
                Device.p256dh == device.p256dh,
                Device.auth == device.auth,
                Device.last_registered_at == device.last_registered_at,
            )
            .returning(Device.id)
        )
        return result.scalar_one_or_none() is not None

    async def preferences(self, user_id: uuid.UUID) -> NotificationPreferences | None:
        return await self.session.get(NotificationPreferences, user_id)

    async def set_preferences(
        self, user_id: uuid.UUID, body: NotificationPreferencesDTO
    ) -> None:
        values = body.model_dump()
        await self.session.execute(
            insert(NotificationPreferences)
            .values(user_id=user_id, **values)
            .on_conflict_do_update(
                index_elements=[NotificationPreferences.user_id], set_=values
            )
        )

    async def settled_path(
        self,
        path_id: uuid.UUID,
        user_id: uuid.UUID,
        fence: datetime.datetime,
        outcome: str,
    ) -> Path | None:
        return await self.session.scalar(
            select(Path).where(
                Path.id == path_id,
                Path.user_id == user_id,
                Path.generation_started_at == fence,
                Path.status == outcome,
            )
        )

    async def device_ids(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        return list(
            (
                await self.session.scalars(
                    select(Device.id)
                    .where(Device.user_id == user_id)
                    .order_by(Device.id)
                )
            ).all()
        )

    async def lock_device(
        self, device_id: uuid.UUID, user_id: uuid.UUID
    ) -> Device | None:
        # Serializes registration/reassignment/delete with an in-flight send.
        return await self.session.scalar(
            select(Device)
            .where(Device.id == device_id, Device.user_id == user_id)
            .with_for_update()
        )
