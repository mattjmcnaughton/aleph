"""Account-owned Notification use cases and guarded, best-effort delivery.

Only a winning durable outline settlement schedules path_ready. A lost fence,
poll or process restart never replays it; an explicit failed-path retry is a new
settlement and can notify again. There is intentionally no outbox or network retry:
a crash after commit may lose a notification, never re-run generation to send it.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import Counter
from datetime import UTC, datetime
from functools import lru_cache
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import structlog

from aleph import events
from aleph.config import settings
from aleph.db import new_session
from aleph.domains.notification_copy import (
    PushMessage,
    brief_published_message,
    path_ready_message,
    reminder_message,
)
from aleph.domains.progression import resume_path
from aleph.domains.push_endpoint import is_allowed_endpoint
from aleph.domains.streaks import compute_streaks
from aleph.dtos.notifications import NotificationPreferencesDTO
from aleph.repositories.lessons import LessonRepository
from aleph.repositories.notifications import NotificationRepository
from aleph.repositories.paths import PathRepository
from aleph.repositories.users import UserRepository
from aleph.services.push import NullPushSender, PushResult, WebPushSender

if TYPE_CHECKING:
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

    async def reminders(self, *, now: datetime | None = None) -> dict[str, int]:
        """Finite keyset scan; claim commits before any bounded network call.

        Cancellation after claim intentionally leaves 'attempted', never replayed.
        The job's step timeout bounds total scan time; no job-run ledger is needed.
        """
        counts: Counter[str] = Counter()
        async with self._sessions() as session:
            start = after = await NotificationRepository(session).reminder_frontier()
        wrapped = False
        while True:
            async with self._sessions() as session:
                accounts = await NotificationRepository(session).reminder_accounts(
                    after, until=start if wrapped else None
                )
            if not accounts:
                if start is not None and not wrapped:
                    async with self._sessions() as session:
                        await NotificationRepository(session).checkpoint_reminder_scan(
                            None
                        )
                        await session.commit()
                    after = None
                    wrapped = True
                    continue
                return dict(counts)
            for account_id in accounts:
                # Commit admission before eligibility locks; a timed-out account
                # must not pin all subsequent ticks to the same prefix.
                async with self._sessions() as session:
                    await NotificationRepository(session).checkpoint_reminder_scan(
                        account_id
                    )
                    await session.commit()
                after = account_id
                counts["examined"] += 1
                try:
                    outcome = await self._reminder(account_id, now)
                    counts[outcome] += 1
                    if outcome == "error":
                        counts["failed"] += 1
                except Exception:
                    counts["failed"] += 1
                    self._log_failure()

    async def _reminder(self, account_id: uuid.UUID, now: datetime | None) -> str:
        async with self._sessions() as session:
            repo = NotificationRepository(session)
            user = await UserRepository(session).lock_by_id(account_id)
            prefs = await get_preferences(session, account_id)
            if user is None or not user.time_zone or not prefs.reminder_enabled:
                return "ineligible"
            # Eligibility belongs to this account's current day, not tick start.
            now = now or datetime.now(UTC)
            local = now.astimezone(ZoneInfo(user.time_zone))
            today = local.date()
            if local.hour < prefs.reminder_hour or not await repo.device_ids(
                account_id
            ):
                return "not_due"
            active = await repo.completion_dates(account_id, user.time_zone)
            if today in active:
                return "active"
            paths = await PathRepository(session).list_for_user_with_effective_status(
                user_id=account_id
            )
            available = await LessonRepository(session).next_lessons(
                [path.id for path, _ in paths]
            )
            resume_id = resume_path([path.id for path, _ in paths], available.keys())
            target = f"/lessons/{available[resume_id].id}" if resume_id else "/"
            message = reminder_message(
                today.isoformat(), target, compute_streaks(active, today=today).current
            )
            if not await repo.claim_reminder(account_id, today, now):
                return "already_claimed"
            await session.commit()
        try:
            outcome = await self._deliver(account_id, message, "reminder_enabled")
        except Exception:
            outcome = "error"
            self._log_failure()
        async with self._sessions() as session:
            await NotificationRepository(session).reminder_outcome(
                account_id, today, outcome
            )
            await session.commit()
        return outcome

    async def brief_published(self, brief_id: uuid.UUID) -> None:
        try:
            async with self._sessions() as session:
                row = await NotificationRepository(session).published_brief(brief_id)
                if row is None:
                    return
                brief, beat = row
                assert brief.number is not None
                message = brief_published_message(
                    str(brief.id), beat.topic, brief.number
                )
                account_id = beat.user_id
            await self._deliver(account_id, message, "brief_published_enabled")
        except Exception:
            self._log_failure()

    async def _deliver(
        self, account_id: uuid.UUID, message: PushMessage, preference: str
    ) -> str:
        async with self._sessions() as session:
            devices = await NotificationRepository(session).device_ids(account_id)
        sender = self.sender or configured_sender(
            settings.vapid_private_key, settings.public_base_url
        )
        outcomes = set()
        for device_id in devices:
            try:
                async with self._sessions() as session:
                    repo = NotificationRepository(session)
                    # Ownership/sign-out cannot overtake an old-account send;
                    # stale gone responses cannot delete replacement subscriptions.
                    device = await repo.lock_device(device_id, account_id)
                    if device is None:
                        continue
                    prefs = await get_preferences(session, account_id)
                    if not getattr(prefs, preference):
                        outcomes.add("disabled")
                        continue
                    async with asyncio.timeout(10):
                        result = await sender.send(device, message)
                    removed = result == PushResult.GONE and await repo.remove_gone(
                        device
                    )
                    await session.commit()
                outcomes.add(result.value)
                if removed:
                    events.emit_device_removed(account_id=account_id, reason="gone")
                events.emit_notification_sent(
                    account_id=account_id, kind=message.kind.value, outcome=result.value
                )
            except Exception:
                outcomes.add("error")
                events.emit_notification_sent(
                    account_id=account_id, kind=message.kind.value, outcome="error"
                )
                self._log_failure()
        # A partial failure is not hidden by another Device's successful send.
        return next(
            (
                value
                for value in ("error", "delivered", "gone", "disabled")
                if value in outcomes
            ),
            "disabled",
        )

    async def path_ready(
        self,
        path_id: uuid.UUID,
        account_id: uuid.UUID,
        fence: datetime,
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
            await self._deliver(account_id, message, "path_ready_enabled")
        except Exception:
            self._log_failure()

    @staticmethod
    def _log_failure() -> None:
        # Never log endpoint, subscription keys, payload, or exception locals.
        with contextlib.suppress(Exception):
            structlog.get_logger(__name__).warning("notification_delivery_failed")


notification_service = NotificationService()
