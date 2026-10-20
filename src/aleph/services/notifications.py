"""Account-owned Notification use cases and guarded, best-effort delivery.

Only a winning durable outline settlement schedules path_ready. A lost fence,
poll or process restart never replays it; an explicit failed-path retry is a new
settlement and can notify again. There is intentionally no outbox or network retry:
a crash after commit may lose a notification, never re-run generation to send it.
"""

from __future__ import annotations

import asyncio
import contextlib
from functools import lru_cache
from typing import TYPE_CHECKING

import structlog

from aleph import events
from aleph.config import settings
from aleph.db import new_session
from aleph.domains.notification_copy import path_ready_message
from aleph.domains.push_endpoint import is_allowed_endpoint
from aleph.dtos.notifications import NotificationPreferencesDTO
from aleph.repositories.notifications import NotificationRepository
from aleph.services.push import NullPushSender, PushResult, WebPushSender

if TYPE_CHECKING:
    import datetime
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.dtos.notifications import RegisterDeviceRequest
    from aleph.services.generation import SessionFactory
    from aleph.services.push import PushSender


class EndpointNotAllowedError(ValueError):
    pass


class DeviceNotFoundError(LookupError):
    pass


@lru_cache(maxsize=1)
def configured_sender(private_key: str, base_url: str) -> PushSender:
    return WebPushSender(private_key, base_url) if private_key else NullPushSender()


def push_public_key() -> str | None:
    sender = configured_sender(settings.vapid_private_key, settings.public_base_url)
    return sender.public_key if isinstance(sender, WebPushSender) else None


async def register_device(
    session: AsyncSession,
    user_id: uuid.UUID,
    body: RegisterDeviceRequest,
    user_agent: str | None,
) -> uuid.UUID:
    if not is_allowed_endpoint(body.endpoint):
        raise EndpointNotAllowedError
    result = await NotificationRepository(session).register(
        user_id, body, user_agent[:512] if user_agent else None
    )
    await session.commit()
    return result


async def delete_device(
    session: AsyncSession, user_id: uuid.UUID, device_id: uuid.UUID
) -> None:
    repo = NotificationRepository(session)
    device = await repo.get_device(device_id)
    if device is not None and device.user_id != user_id:
        raise DeviceNotFoundError
    await repo.delete_device(device_id, user_id)
    await session.commit()


async def get_preferences(
    session: AsyncSession, user_id: uuid.UUID
) -> NotificationPreferencesDTO:
    row = await NotificationRepository(session).preferences(user_id)
    if row is None:
        return NotificationPreferencesDTO()
    return NotificationPreferencesDTO(
        reminder_enabled=row.reminder_enabled,
        reminder_hour=row.reminder_hour,
        path_ready_enabled=row.path_ready_enabled,
        brief_published_enabled=row.brief_published_enabled,
    )


async def put_preferences(
    session: AsyncSession, user_id: uuid.UUID, body: NotificationPreferencesDTO
) -> NotificationPreferencesDTO:
    await NotificationRepository(session).set_preferences(user_id, body)
    await session.commit()
    return body


class NotificationService:
    def __init__(
        self,
        *,
        sender: PushSender | None = None,
        session_factory: SessionFactory = new_session,
    ) -> None:
        self.sender = sender
        self._sessions = session_factory

    async def path_ready(
        self,
        path_id: uuid.UUID,
        account_id: uuid.UUID,
        fence: datetime.datetime,
        outcome: str,
    ) -> None:
        """Load durable state and current ownership; contain every send failure."""
        try:
            async with self._sessions() as session:
                repo = NotificationRepository(session)
                path = await repo.settled_path(path_id, account_id, fence, outcome)
                if path is None:
                    return
                message = path_ready_message(str(path_id), path.display_title, outcome)
                devices = await repo.device_ids(account_id)
            sender = self.sender or configured_sender(
                settings.vapid_private_key, settings.public_base_url
            )
            for device_id in devices:
                try:
                    async with self._sessions() as session:
                        repo = NotificationRepository(session)
                        # Hold this Device's lock through the bounded send. A
                        # re-registration/sign-out cannot overtake an old-account
                        # send, nor can a stale gone result delete its replacement.
                        device = await repo.lock_device(device_id, account_id)
                        if device is None:
                            continue
                        prefs = await get_preferences(session, account_id)
                        if not prefs.path_ready_enabled:
                            continue
                        async with asyncio.timeout(10):
                            result = await sender.send(device, message)
                        removed = result == PushResult.GONE and await repo.remove_gone(
                            device
                        )
                        await session.commit()
                    if removed:
                        events.emit_device_removed(account_id=account_id, reason="gone")
                    events.emit_notification_sent(
                        account_id=account_id,
                        kind=message.kind.value,
                        outcome=result.value,
                    )
                except Exception:
                    events.emit_notification_sent(
                        account_id=account_id, kind=message.kind.value, outcome="error"
                    )
                    self._log_failure()
        except Exception:
            self._log_failure()

    @staticmethod
    def _log_failure() -> None:
        # Never log endpoint, subscription keys, payload, or exception locals.
        with contextlib.suppress(Exception):
            structlog.get_logger(__name__).warning("notification_delivery_failed")


notification_service = NotificationService()
