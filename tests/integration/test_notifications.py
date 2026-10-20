"""Real fenced generation and notification delivery, with no live providers."""

import asyncio

import pytest
from sqlalchemy import select

from aleph import db
from aleph.dtos.notifications import NotificationPreferencesDTO, RegisterDeviceRequest
from aleph.models import Device, Level, Path, PathStatus
from aleph.repositories.notifications import NotificationRepository
from aleph.repositories.paths import PathRepository
from aleph.services.generation import GenerationOrchestrator
from aleph.services.notifications import (
    NotificationService,
    put_preferences,
    register_device,
)
from aleph.services.push import InMemoryPushSender, PushResult

from .conftest import CollectingSpawn, stub_resolver
from .test_push_api import BODY, make_user


@pytest.mark.anyio
@pytest.mark.parametrize("escape", ["infra", "lost_fence", "cancel"])
async def test_outline_infrastructure_escape_notifies_only_committed_winner(
    escape, capfire
):
    owner = await make_user()
    async with db.new_session() as session:
        await register_device(
            session, owner.id, RegisterDeviceRequest.model_validate(BODY), None
        )
        path = await PathRepository(session).create(
            user_id=owner.id, topic="Algebra", level=Level.NEW_TO_IT
        )
        await session.commit()

    class EscapingOrchestrator(GenerationOrchestrator):
        async def _persist_outline(self, path_id, fence, outline):
            if escape == "cancel":
                raise asyncio.CancelledError
            if escape == "lost_fence":
                async with db.new_session() as session:
                    repo = PathRepository(session)
                    assert await repo.mark_failed(path_id, fence=fence)
                    await session.commit()
                    assert await repo.claim_outline_for_retry(path_id)
                    await session.commit()
            raise RuntimeError("synthetic persistence failure")

    sender = InMemoryPushSender()
    spawn = CollectingSpawn()
    orchestrator = EscapingOrchestrator(
        spawn=spawn,
        resolve_model_fn=stub_resolver(),
        prefetch_n=0,
        notifications=NotificationService(sender=sender),
    )
    try:
        if escape == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await orchestrator.run_outline_task(path.id)
        else:
            await orchestrator.run_outline_task(path.id)
        await spawn.drain()
        assert len(sender.pushes) == (1 if escape == "infra" else 0)
        if sender.pushes:
            assert sender.pushes[0].account_id == str(owner.id)
        async with db.new_session() as session:
            saved = await session.get(Path, path.id)
            assert saved is not None
            assert saved.status == (
                PathStatus.FAILED if escape == "infra" else PathStatus.GENERATING
            )
        exported = repr(capfire.exporter.exported_spans_as_dict())
        assert "outline_generated" not in exported
        assert ("outline_task_failed" in exported) == (escape != "cancel")
    finally:
        await spawn.cancel_pending()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "topic,outcome,body",
    [
        ("Algebra", "ready", "Your path on Algebra is ready."),
        ("[force-refusal]", "refused", "We couldn't make a path on that topic."),
        (
            "[force-outline-failure]",
            "failed",
            "Your path didn't finish building. Tap to retry.",
        ),
    ],
)
async def test_fenced_settlement_sends_once_after_commit(topic, outcome, body):
    owner, other = await make_user(), await make_user()
    async with db.new_session() as session:
        await register_device(
            session, owner.id, RegisterDeviceRequest.model_validate(BODY), None
        )
        await register_device(
            session,
            other.id,
            RegisterDeviceRequest.model_validate(
                {**BODY, "endpoint": "https://push.apple.com/other"}
            ),
            None,
        )
        path = await PathRepository(session).create(
            user_id=owner.id, topic=topic, level=Level.NEW_TO_IT
        )
        await session.commit()

    class DurableSender(InMemoryPushSender):
        async def send(self, device, message):
            async with db.new_session() as session:
                persisted = await session.get(Path, path.id)
                assert persisted is not None
                assert persisted.status.value == outcome
            return await super().send(device, message)

    sender = DurableSender()
    spawn = CollectingSpawn()
    notifier = NotificationService(sender=sender)
    first = GenerationOrchestrator(
        spawn=spawn,
        resolve_model_fn=stub_resolver(),
        prefetch_n=0,
        notifications=notifier,
    )
    second = GenerationOrchestrator(
        spawn=spawn,
        resolve_model_fn=stub_resolver(),
        prefetch_n=0,
        notifications=notifier,
    )
    try:
        await asyncio.gather(
            first.run_outline_task(path.id), second.run_outline_task(path.id)
        )
        await spawn.drain()
        assert len(sender.pushes) == 1
        assert sender.pushes[0].account_id == str(owner.id)
        assert sender.pushes[0].payload["notification"]["body"] == body
        await first.run_outline_task(path.id)
        await spawn.drain()
        assert len(sender.pushes) == 1
        if outcome == "failed":
            await second.run_outline_task(path.id, retry=True)
            await spawn.drain()
            assert len(sender.pushes) == 2
    finally:
        await spawn.cancel_pending()


@pytest.mark.anyio
async def test_preferences_ownership_and_gone():
    owner, other = await make_user(), await make_user()
    sender = InMemoryPushSender(result=PushResult.GONE)
    notifier = NotificationService(sender=sender)
    async with db.new_session() as session:
        device_id = await register_device(
            session, owner.id, RegisterDeviceRequest.model_validate(BODY), None
        )
        path = await PathRepository(session).create(
            user_id=owner.id, topic="Algebra", level=Level.NEW_TO_IT
        )
        await session.commit()
        fence = await PathRepository(session).claim_outline(path.id)
        assert fence is not None
        await session.commit()
        await PathRepository(session).mark_failed(path.id, fence=fence)
        await session.commit()
        await put_preferences(
            session, owner.id, NotificationPreferencesDTO(path_ready_enabled=False)
        )
    await notifier.path_ready(path.id, owner.id, fence, "failed")
    await notifier.path_ready(path.id, other.id, fence, "failed")
    assert not sender.pushes
    async with db.new_session() as session:
        await put_preferences(session, owner.id, NotificationPreferencesDTO())
    await notifier.path_ready(path.id, owner.id, fence, "failed")
    assert len(sender.pushes) == 1
    async with db.new_session() as session:
        assert await session.get(Device, device_id) is None
        assert (
            await session.scalar(select(Path.status).where(Path.id == path.id))
        ) == PathStatus.FAILED


@pytest.mark.anyio
@pytest.mark.parametrize("result", [PushResult.ERROR, PushResult.DISABLED])
async def test_non_success_does_not_remove_device_or_change_generation(result):
    owner = await make_user()
    sender = InMemoryPushSender(result=result)
    async with db.new_session() as session:
        device_id = await register_device(
            session, owner.id, RegisterDeviceRequest.model_validate(BODY), None
        )
        path = await PathRepository(session).create(
            user_id=owner.id, topic="Algebra", level=Level.NEW_TO_IT
        )
        await session.commit()
        fence = await PathRepository(session).claim_outline(path.id)
        assert fence is not None
        await session.commit()
        assert await PathRepository(session).mark_failed(path.id, fence=fence)
        await session.commit()
    await NotificationService(sender=sender).path_ready(
        path.id, owner.id, fence, "failed"
    )
    async with db.new_session() as session:
        assert await session.get(Device, device_id) is not None
        saved = await session.get(Path, path.id)
        assert saved is not None and saved.status == PathStatus.FAILED


@pytest.mark.anyio
async def test_sender_exception_does_not_prevent_other_devices():
    owner = await make_user()

    class FailingFirst(InMemoryPushSender):
        attempts = 0

        async def send(self, device, message):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("delivery failed")
            return await super().send(device, message)

    sender = FailingFirst()
    async with db.new_session() as session:
        for suffix in ("one", "two"):
            await register_device(
                session,
                owner.id,
                RegisterDeviceRequest.model_validate(
                    {**BODY, "endpoint": "https://push.apple.com/" + suffix}
                ),
                None,
            )
        path = await PathRepository(session).create(
            user_id=owner.id, topic="Algebra", level=Level.NEW_TO_IT
        )
        await session.commit()
    spawn = CollectingSpawn()
    orchestrator = GenerationOrchestrator(
        spawn=spawn,
        resolve_model_fn=stub_resolver(),
        prefetch_n=0,
        notifications=NotificationService(sender=sender),
    )
    try:
        await orchestrator.run_outline_task(path.id)
        await spawn.drain()
        assert sender.attempts == 2
        assert len(sender.pushes) == 1
        async with db.new_session() as session:
            saved = await session.get(Path, path.id)
            assert saved is not None and saved.status == PathStatus.READY
    finally:
        await spawn.cancel_pending()


@pytest.mark.anyio
async def test_old_fence_and_rolled_back_settlement_never_notify():
    owner = await make_user()
    sender = InMemoryPushSender()
    notifier = NotificationService(sender=sender)
    async with db.new_session() as session:
        await register_device(
            session, owner.id, RegisterDeviceRequest.model_validate(BODY), None
        )
        path = await PathRepository(session).create(
            user_id=owner.id, topic="Algebra", level=Level.NEW_TO_IT
        )
        await session.commit()
        first_fence = await PathRepository(session).claim_outline(path.id)
        assert first_fence is not None
        path_id = path.id
        await session.commit()
        assert await PathRepository(session).mark_failed(path_id, fence=first_fence)
        await session.rollback()
    await notifier.path_ready(path_id, owner.id, first_fence, "failed")
    assert not sender.pushes
    async with db.new_session() as session:
        assert await PathRepository(session).mark_failed(path_id, fence=first_fence)
        await session.commit()
        second_fence = await PathRepository(session).claim_outline_for_retry(path_id)
        assert second_fence is not None and second_fence != first_fence
        await session.commit()
        # An obsolete worker cannot settle the new claim or notify it.
        assert not await PathRepository(session).mark_failed(path_id, fence=first_fence)
        await session.rollback()
    await notifier.path_ready(path_id, owner.id, first_fence, "failed")
    await notifier.path_ready(path_id, owner.id, second_fence, "ready")
    assert not sender.pushes
    async with db.new_session() as session:
        assert await PathRepository(session).mark_failed(path_id, fence=second_fence)
        await session.commit()
    # Matching terminal status must not let an obsolete generation notify.
    await notifier.path_ready(path_id, owner.id, first_fence, "failed")
    assert not sender.pushes
    await notifier.path_ready(path_id, owner.id, second_fence, "failed")
    assert len(sender.pushes) == 1


@pytest.mark.anyio
async def test_reregister_during_send_is_serialized_and_new_registration_survives():
    owner, other = await make_user(), await make_user()
    entered, release = asyncio.Event(), asyncio.Event()

    class HeldSender(InMemoryPushSender):
        async def send(self, device, message):
            entered.set()
            await release.wait()
            return await super().send(device, message)

    sender = HeldSender(result=PushResult.GONE)
    async with db.new_session() as session:
        device_id = await register_device(
            session, owner.id, RegisterDeviceRequest.model_validate(BODY), None
        )
        path = await PathRepository(session).create(
            user_id=owner.id, topic="Algebra", level=Level.NEW_TO_IT
        )
        await session.commit()
        fence = await PathRepository(session).claim_outline(path.id)
        assert fence is not None
        await session.commit()
        assert await PathRepository(session).mark_failed(path.id, fence=fence)
        await session.commit()
    notification = asyncio.create_task(
        NotificationService(sender=sender).path_ready(
            path.id, owner.id, fence, "failed"
        )
    )

    async def move():
        async with db.new_session() as session:
            return await register_device(
                session, other.id, RegisterDeviceRequest.model_validate(BODY), None
            )

    reassignment = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        reassignment = asyncio.create_task(move())
        await asyncio.sleep(0.05)
        assert not reassignment.done()
        release.set()
        await asyncio.wait_for(notification, 2)
        moved_id = await asyncio.wait_for(reassignment, 2)
        async with db.new_session() as session:
            current = await session.get(Device, moved_id)
            assert current is not None and current.user_id == other.id
        # Gone removes the old registration; an upsert after it creates a new ID.
        assert moved_id != device_id
        assert sender.pushes[0].account_id == str(owner.id)
    finally:
        release.set()
        await notification
        if reassignment is not None:
            await reassignment


@pytest.mark.anyio
async def test_stale_gone_cleanup_cannot_remove_refreshed_or_reassigned_device():
    owner, other = await make_user(), await make_user()
    async with db.new_session() as session:
        device_id = await register_device(
            session, owner.id, RegisterDeviceRequest.model_validate(BODY), None
        )
        snapshot = await session.get(Device, device_id)
        assert snapshot is not None
    async with db.new_session() as session:
        await register_device(
            session, other.id, RegisterDeviceRequest.model_validate(BODY), None
        )
    async with db.new_session() as session:
        assert not await NotificationRepository(session).remove_gone(snapshot)
        await session.commit()
        current = await session.get(Device, device_id)
        assert current is not None and current.user_id == other.id
