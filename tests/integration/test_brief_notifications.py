"""Published settlement notifications use durable state and contain failures."""

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import update
from structlog.testing import capture_logs

from aleph import db
from aleph.dtos.notifications import RegisterDeviceRequest
from aleph.models import Beat, BeatResearchState, BriefKind
from aleph.repositories.beats import BeatRepository
from aleph.services.briefing import BriefingService
from aleph.services.notifications import NotificationService, register_device
from aleph.services.push import InMemoryPushSender

from .test_briefing import (
    _briefs_for_beat,
    _doc,
    _FakeRetriever,
    _finding_payload,
    _make_beat,
    _PipelineResponder,
    _resolver,
)
from .test_push_api import BODY, make_user


@pytest.mark.anyio
@pytest.mark.parametrize("entry", ["self_claim", "preclaimed", "retry"])
@pytest.mark.parametrize(
    "mode",
    ["published", "skipped", "error", "notifier_error", "lost_fence", "disabled"],
)
async def test_research_notifies_only_after_durable_publication(mode, entry):
    owner = await make_user()
    async with db.new_session() as session:
        await register_device(
            session, owner.id, RegisterDeviceRequest.model_validate(BODY), None
        )
    beat_id = await _make_beat(user=owner)

    class DurableSender(InMemoryPushSender):
        async def send(self, device, message):
            briefs = await _briefs_for_beat(beat_id)
            assert len(briefs) == 1 and briefs[0].kind == BriefKind.PUBLISHED
            assert message.target == f"/briefs/{briefs[0].id}"
            if mode == "error":
                raise RuntimeError("synthetic transport error")
            return await super().send(device, message)

    sender = DurableSender()

    class FailingNotifier(NotificationService):
        async def brief_published(self, brief_id):
            raise RuntimeError("synthetic notifier failure")

    class RacingRetriever(_FakeRetriever):
        async def search(self, queries, *, since=None):
            if mode == "lost_fence":
                async with db.new_session() as session:
                    await session.execute(
                        update(Beat)
                        .where(Beat.id == beat_id)
                        .values(
                            research_started_at=datetime.now(UTC) + timedelta(seconds=1)
                        )
                    )
                    await session.commit()
            return await super().search(queries, since=since)

    fence = None
    async with db.new_session() as session:
        repo = BeatRepository(session)
        if entry == "retry":
            await session.execute(
                update(Beat)
                .where(Beat.id == beat_id)
                .values(research_state=BeatResearchState.FAILED)
            )
            fence = await repo.claim_research_for_retry(beat_id)
        elif entry == "preclaimed":
            fence = await repo.claim_research(beat_id)
        if mode == "disabled":
            from aleph.dtos.notifications import NotificationPreferencesDTO
            from aleph.services.notifications import put_preferences

            await put_preferences(
                session,
                owner.id,
                NotificationPreferencesDTO(brief_published_enabled=False),
            )
        await session.commit()
    responder = _PipelineResponder(
        researcher=(
            "findings",
            {
                "findings": []
                if mode == "skipped"
                else [
                    _finding_payload(
                        "Something material happened", ["https://example.com/a"]
                    )
                ]
            },
        ),
        analyst=("detail", {"detail": ""})
        if mode == "skipped"
        else (
            "title",
            {
                "title": "A material change",
                "body_markdown": "Something changed [1].",
                "cited_urls": ["https://example.com/a"],
            },
        ),
    )
    service = BriefingService(
        retriever=RacingRetriever(documents=[_doc("https://example.com/a")]),
        resolve_model_fn=_resolver(FunctionModel(responder)),
        notifications=FailingNotifier()
        if mode == "notifier_error"
        else NotificationService(sender=sender),
    )
    with capture_logs() as logs:
        await service.run_research(beat_id, date(2026, 10, 20), fence=fence)
    if mode == "notifier_error":
        logged_events = [row["event"] for row in logs]
        assert "notification_delivery_failed" in logged_events
        assert "brief_research_persist_failed" not in logged_events
    briefs = await _briefs_for_beat(beat_id)
    if mode == "lost_fence":
        assert not briefs and not sender.pushes
        return
    assert len(briefs) == 1
    assert briefs[0].kind == (
        BriefKind.SKIPPED if mode == "skipped" else BriefKind.PUBLISHED
    )
    assert len(sender.pushes) == (1 if mode == "published" else 0)
    async with db.new_session() as session:
        beat = await session.get(Beat, beat_id)
        assert beat is not None and beat.research_state == BeatResearchState.IDLE
