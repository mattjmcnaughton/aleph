"""Notification persistence; caller owns transactions and delivery policy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from aleph.models import Device, NotificationPreferences, Path

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
