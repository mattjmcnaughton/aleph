"""Durable Reminder attempts with real Postgres and no push providers."""

import asyncio
from datetime import date, datetime

import pytest
from sqlalchemy import select

from aleph import db
from aleph.dtos.notifications import NotificationPreferencesDTO, RegisterDeviceRequest
from aleph.models import Device, Level, ReminderSend, User
from aleph.repositories.lessons import LessonRepository
from aleph.repositories.paths import PathRepository
from aleph.repositories.units import UnitRepository
from aleph.services.notifications import (
    NotificationService,
    put_preferences,
    register_device,
)
from aleph.services.push import InMemoryPushSender, PushResult

from .test_push_api import BODY, make_user


async def opted_in(zone="America/New_York", hour=20):
    owner = await make_user()
    async with db.new_session() as session:
        user = await session.get(User, owner.id)
        assert user is not None
        user.time_zone = zone
        await put_preferences(
            session,
            owner.id,
            NotificationPreferencesDTO(reminder_enabled=True, reminder_hour=hour),
        )
        await register_device(
            session,
            owner.id,
            RegisterDeviceRequest.model_validate(
                {**BODY, "endpoint": f"{BODY['endpoint']}-{owner.id}"}
            ),
            None,
        )
    return owner


@pytest.mark.anyio
async def test_scan_crossing_midnight_does_not_claim_yesterday(monkeypatch):
    from aleph.services import notifications

    first, later = sorted(
        [await opted_in(zone="UTC"), await opted_in(zone="UTC")], key=lambda u: u.id
    )
    instant = datetime.fromisoformat("2026-10-20T23:59:00+00:00")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant

    class AdvancingSender(InMemoryPushSender):
        async def send(self, device, message):
            nonlocal instant
            instant = datetime.fromisoformat("2026-10-21T00:01:00+00:00")
            return await super().send(device, message)

    monkeypatch.setattr(notifications, "datetime", Clock)
    sender = AdvancingSender()
    await NotificationService(sender=sender).reminders()
    assert len(sender.pushes) == 1
    async with db.new_session() as session:
        rows = list(await session.scalars(select(ReminderSend)))
        assert [(row.user_id, row.local_date) for row in rows] == [
            (first.id, date(2026, 10, 20))
        ]
        assert await session.get(ReminderSend, (later.id, date(2026, 10, 20))) is None


@pytest.mark.anyio
async def test_timed_scan_resumes_past_locked_prefix():
    first, later = sorted([await opted_in(), await opted_in()], key=lambda u: u.id)
    sender = InMemoryPushSender()
    now = datetime.fromisoformat("2026-10-21T01:00:00+00:00")
    async with db.new_session() as blocker:
        await blocker.execute(select(User).where(User.id == first.id).with_for_update())
        for _ in range(2):
            try:
                async with asyncio.timeout(0.5):
                    await NotificationService(sender=sender).reminders(now=now)
            except TimeoutError:
                pass
        async with db.new_session() as session:
            row = await session.get(ReminderSend, (later.id, date(2026, 10, 20)))
            assert row is not None and row.outcome == "delivered"
            assert (
                await session.get(ReminderSend, (first.id, date(2026, 10, 20))) is None
            )
        assert len(sender.pushes) == 1
    await NotificationService(sender=sender).reminders(now=now)
    assert len(sender.pushes) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("first_errors", [False, True])
async def test_mixed_device_results_preserve_error_and_never_replay(first_errors):
    owner = await opted_in()
    async with db.new_session() as session:
        first_id = await session.scalar(
            select(Device.id).where(Device.user_id == owner.id)
        )
        await register_device(
            session, owner.id, RegisterDeviceRequest.model_validate(BODY), None
        )

    class MixedSender(InMemoryPushSender):
        async def send(self, device, message):
            await super().send(device, message)
            return (
                PushResult.ERROR
                if (device.id == first_id) == first_errors
                else PushResult.DELIVERED
            )

    sender = MixedSender()
    service = NotificationService(sender=sender)
    now = datetime.fromisoformat("2026-10-21T01:00:00+00:00")
    counts = await service.reminders(now=now)
    assert counts["error"] == counts["failed"] == 1
    await service.reminders(now=now)
    assert len(sender.pushes) == 2
    async with db.new_session() as session:
        row = await session.get(ReminderSend, (owner.id, date(2026, 10, 20)))
        assert row is not None and row.outcome == "error"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "instant,hour,expected",
    [
        ("2026-03-08T06:59:00+00:00", 2, 0),
        ("2026-03-08T07:00:00+00:00", 2, 1),
        ("2026-11-01T05:30:00+00:00", 1, 1),
        ("2026-11-01T06:30:00+00:00", 1, 1),
        ("2026-10-21T03:59:00+00:00", 20, 1),
        ("2026-10-21T04:00:00+00:00", 20, 0),
    ],
)
async def test_local_hour_dst_and_late_tick(instant, hour, expected):
    await opted_in(hour=hour)
    sender = InMemoryPushSender()
    service = NotificationService(sender=sender)
    now = datetime.fromisoformat(instant)
    await service.reminders(now=now)
    await service.reminders(now=now)
    assert len(sender.pushes) == expected


@pytest.mark.anyio
async def test_concurrent_claim_committed_before_delivery():
    owner = await opted_in()

    class DurableSender(InMemoryPushSender):
        async def send(self, device, message):
            async with db.new_session() as session:
                row = await session.get(ReminderSend, (owner.id, date(2026, 10, 20)))
                assert row is not None and row.outcome == "attempted"
            return await super().send(device, message)

    sender = DurableSender()
    service = NotificationService(sender=sender)
    now = datetime.fromisoformat("2026-10-21T01:00:00+00:00")
    await asyncio.gather(*(service.reminders(now=now) for _ in range(4)))
    assert len(sender.pushes) == 1
    async with db.new_session() as session:
        rows = (await session.scalars(select(ReminderSend))).all()
        assert len(rows) == 1 and rows[0].outcome == PushResult.DELIVERED.value


@pytest.mark.anyio
async def test_cancel_after_claim_never_retries():
    owner = await opted_in()

    class KilledSender(InMemoryPushSender):
        async def send(self, device, message):
            raise asyncio.CancelledError

    now = datetime.fromisoformat("2026-10-21T01:00:00+00:00")
    with pytest.raises(asyncio.CancelledError):
        await NotificationService(sender=KilledSender()).reminders(now=now)
    sender = InMemoryPushSender()
    await NotificationService(sender=sender).reminders(now=now)
    assert not sender.pushes
    async with db.new_session() as session:
        row = await session.get(ReminderSend, (owner.id, date(2026, 10, 20)))
        assert row is not None and row.outcome == "attempted"


async def path_with_completions(owner, instants):
    async with db.new_session() as session:
        path = await PathRepository(session).create(
            user_id=owner.id, topic="Algebra", level=Level.NEW_TO_IT
        )
        unit = await UnitRepository(session).create(
            path_id=path.id, position=1, title="One", summary="One"
        )
        for position, instant in enumerate([*instants, None], 1):
            lesson = await LessonRepository(session).create(
                unit_id=unit.id,
                path_id=path.id,
                position_in_path=position,
                position_in_unit=position,
                title=f"Lesson {position}",
            )
            lesson.completed_at = datetime.fromisoformat(instant) if instant else None
        await session.commit()
        return lesson.id


@pytest.mark.anyio
@pytest.mark.parametrize(
    "completion,sends,body",
    [
        ("2026-03-09T04:30:00+00:00", 0, None),  # today, EDT
        (
            "2026-03-08T04:30:00+00:00",
            1,
            "Pick up where you left off.",
        ),  # March 7, EST: today's EDT offset would wrongly call this March 8.
        ("2026-03-08T05:30:00+00:00", 1, "Keep your 1-day streak going."),
    ],
)
async def test_active_day_and_historical_iana_offsets(completion, sends, body):
    owner = await opted_in()
    await path_with_completions(owner, [completion])
    sender = InMemoryPushSender()
    await NotificationService(sender=sender).reminders(
        now=datetime.fromisoformat("2026-03-10T01:00:00+00:00")
    )
    assert len(sender.pushes) == sends
    if sends:
        assert sender.pushes[0].payload["notification"]["body"] == body


@pytest.mark.anyio
async def test_resume_target_matches_api_order_not_path_creation():
    owner = await opted_in()
    resume = await path_with_completions(owner, ["2026-10-19T16:00:00+00:00"])
    await path_with_completions(owner, [])  # newer, never worked
    sender = InMemoryPushSender()
    await NotificationService(sender=sender).reminders(
        now=datetime.fromisoformat("2026-10-21T01:00:00+00:00")
    )
    assert (
        sender.pushes[0]
        .payload["notification"]["navigate"]
        .endswith(f"/lessons/{resume}")
    )


@pytest.mark.anyio
@pytest.mark.parametrize("missing", ["zone", "device", "opt_in"])
async def test_requires_all_prerequisites(missing):
    owner = await opted_in(zone=None if missing == "zone" else "America/New_York")
    async with db.new_session() as session:
        if missing == "device":
            device = await session.scalar(
                select(Device).where(Device.user_id == owner.id)
            )
            await session.delete(device)
        if missing == "opt_in":
            await put_preferences(session, owner.id, NotificationPreferencesDTO())
        await session.commit()
    sender = InMemoryPushSender()
    await NotificationService(sender=sender).reminders(
        now=datetime.fromisoformat("2026-10-21T01:00:00+00:00")
    )
    assert not sender.pushes
    async with db.new_session() as session:
        assert not (await session.scalars(select(ReminderSend))).all()


@pytest.mark.anyio
async def test_changed_zone_and_fold_do_not_replay_same_date():
    owner = await opted_in(hour=1)
    sender = InMemoryPushSender()
    service = NotificationService(sender=sender)
    await service.reminders(now=datetime.fromisoformat("2026-11-01T05:30:00+00:00"))
    await service.reminders(now=datetime.fromisoformat("2026-11-01T06:30:00+00:00"))
    async with db.new_session() as session:
        user = await session.get(User, owner.id)
        assert user is not None
        user.time_zone = "Europe/London"
        await session.commit()
    await service.reminders(now=datetime.fromisoformat("2026-11-01T06:30:00+00:00"))
    assert len(sender.pushes) == 1
    await service.reminders(now=datetime.fromisoformat("2026-11-02T06:30:00+00:00"))
    assert len(sender.pushes) == 2
    async with db.new_session() as session:
        assert {
            row.local_date
            for row in (await session.scalars(select(ReminderSend))).all()
        } == {date(2026, 11, 1), date(2026, 11, 2)}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "outcome", [PushResult.DISABLED, PushResult.ERROR, PushResult.GONE]
)
async def test_non_delivery_outcomes_are_durable_and_not_retried(outcome):
    owner = await opted_in()
    sender = InMemoryPushSender(result=outcome)
    service = NotificationService(sender=sender)
    now = datetime.fromisoformat("2026-10-21T01:00:00+00:00")
    counts = await service.reminders(now=now)
    await service.reminders(now=now)
    assert len(sender.pushes) == 1
    assert counts[outcome.value] == 1
    async with db.new_session() as session:
        row = await session.get(ReminderSend, (owner.id, date(2026, 10, 20)))
        assert row is not None and row.outcome == outcome.value
        assert bool(await session.scalar(select(Device))) == (
            outcome != PushResult.GONE
        )
