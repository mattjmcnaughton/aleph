"""Durable operations against isolated PostgreSQL, never a paid model."""

import asyncio
import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aleph import db
from aleph.agents.learning_tutor import LearningTutorOutput
from aleph.config import Settings
from aleph.domains.learning_recommendations import RecommendationConflict
from aleph.dtos.learning_tutor import OperationRequest
from aleph.models import Level, Path, UserFeatureOverride
from aleph.models.learning_tutor import LearningTutorOperation
from aleph.services.learning_tutor import LearningTutorService
from aleph.services.learning_tutor_generation import (
    LearningTutorBusy,
    LearningTutorGeneration,
    LearningTutorQuotaExceeded,
)

from .test_learning_tutor import account, card, client, operation


class FakeProvider:
    def __init__(self):
        self.calls = 0

    async def __call__(self, deps):
        self.calls += 1
        return LearningTutorOutput(reply="Try continuing your learning."), 5, 9


@pytest.mark.anyio
async def test_cold_refresh_replays_without_admission():
    user = await account()
    provider = FakeProvider()
    runtime = LearningTutorGeneration(provider=provider)
    request = OperationRequest(
        request_id=uuid.uuid4(), revision=0, epoch=0, kind="refresh"
    )
    async with db.async_session() as session:
        result = await runtime.admit(LearningTutorService(session, user), request)
        assert result.status == "succeeded"
        assert result.output is not None
        assert result.output["outcome"] == "needs_context"
        replay = await runtime.admit(LearningTutorService(session, user), request)
        assert replay.id == result.id
    assert provider.calls == 0


@pytest.mark.anyio
async def test_turn_publishes_whole_output_and_replays_after_revision():
    user = await account()
    provider = FakeProvider()
    pending = []
    runtime = LearningTutorGeneration(provider=provider, spawn=pending.append)
    request = OperationRequest(
        request_id=uuid.uuid4(),
        revision=0,
        epoch=0,
        kind="turn",
        content="Electricity interests me",
    )
    async with db.async_session() as session:
        result = await runtime.admit(LearningTutorService(session, user), request)
        assert result.status == "running"
    await pending.pop()
    async with db.async_session() as session:
        service = LearningTutorService(session, user)
        state = await service.read()
        assert state.revision == 1
        assert len(state.history.items) == 1
        assert state.history.items[0].output is not None
        assert state.history.items[0].output["reply"] == "Try continuing your learning."
        replay = await runtime.admit(service, request)
        assert replay.id == result.id and replay.status == "succeeded"
    assert provider.calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize("race", ["clear", "focus", "flag", "expire"])
async def test_delayed_result_cannot_publish_after_fence(race):
    user = await account()
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(deps):
        started.set()
        await release.wait()
        return LearningTutorOutput(reply="Late"), 1, 2

    tasks = []
    runtime = LearningTutorGeneration(
        provider=delayed, spawn=lambda c: tasks.append(asyncio.create_task(c))
    )
    request = OperationRequest(
        request_id=uuid.uuid4(), revision=0, epoch=0, kind="turn", content="Interest"
    )
    async with db.async_session() as session:
        result = await runtime.admit(LearningTutorService(session, user), request)
    await started.wait()
    async with db.async_session() as session:
        service = LearningTutorService(session, user)
        if race == "clear":
            await service.clear(revision=0)
        elif race == "focus":
            await service.save_focus("New focus", revision=0)
        elif race == "flag":
            await session.execute(
                update(UserFeatureOverride)
                .where(UserFeatureOverride.user_id == user.id)
                .values(enabled=False)
            )
            await session.commit()
        else:
            row = await service.repository.operation(user.id, result.id)
            assert row is not None
            row.expires_at = row.started_at - timedelta(seconds=1)
            await session.commit()
    release.set()
    await tasks[0]
    async with db.async_session() as session:
        row = await session.get(LearningTutorOperation, result.id)
        assert row is not None
        assert row.status == "failed" and row.output is None
        assert (row.input_tokens, row.output_tokens) == (1, 2)
        assert (
            row.error_code
            == {
                "clear": None,
                "focus": "context_changed",
                "flag": "feature_disabled",
                "expire": "interrupted",
            }[race]
        )


@pytest.mark.anyio
async def test_cross_session_admission_replay_body_and_quota_after_clear():
    user = await account()
    pending = []
    runtime = LearningTutorGeneration(
        provider=FakeProvider(),
        spawn=pending.append,
        config=Settings(rate_limit_learning_tutor_per_day=1),
    )
    request = OperationRequest(
        request_id=uuid.uuid4(), revision=0, epoch=0, kind="turn", content="Interest"
    )

    async def submit(body):
        async with db.async_session() as session:
            return await runtime.admit(LearningTutorService(session, user), body)

    results = await asyncio.gather(
        submit(request),
        submit(request.model_copy(update={"request_id": uuid.uuid4()})),
        return_exceptions=True,
    )
    assert sum(isinstance(result, LearningTutorBusy) for result in results) == 1
    admitted = next(
        result for result in results if not isinstance(result, BaseException)
    )
    async with db.async_session() as session:
        row = await session.get(LearningTutorOperation, admitted.id)
        assert row is not None
        accepted_request = OperationRequest.model_validate(row.input)
    with pytest.raises(RecommendationConflict, match="request_reused"):
        await submit(accepted_request.model_copy(update={"content": "Changed"}))
    await pending.pop()
    async with db.async_session() as session:
        await LearningTutorService(session, user).clear(revision=1)
    with pytest.raises(RecommendationConflict, match="stale_state"):
        await submit(accepted_request)
    with pytest.raises(LearningTutorQuotaExceeded):
        await submit(
            request.model_copy(
                update={"request_id": uuid.uuid4(), "revision": 2, "epoch": 1}
            )
        )


@pytest.mark.anyio
async def test_metadata_context_excludes_path_title_and_other_account():
    user, other = await account(), await account()
    captured = []

    async def provider(deps):
        captured.append(json.loads(deps.context))
        assert "PRIVATE" not in deps.context
        return LearningTutorOutput(reply="Continue"), 1, 1

    pending = []
    runtime = LearningTutorGeneration(provider=provider, spawn=pending.append)
    async with db.async_session() as session:
        session.add_all(
            [
                Path(
                    user_id=user.id,
                    topic="Electricity",
                    title="PRIVATE title",
                    level=Level.NEW_TO_IT,
                ),
                Path(
                    user_id=other.id, topic="PRIVATE other topic", level=Level.NEW_TO_IT
                ),
            ]
        )
        await session.commit()
        await runtime.admit(
            LearningTutorService(session, user),
            OperationRequest(
                request_id=uuid.uuid4(),
                revision=0,
                epoch=0,
                kind="turn",
                content="Interest",
            ),
        )
    await pending.pop()
    assert captured[0]["paths"][0]["topic"] == "Electricity"


@pytest.mark.anyio
async def test_invalid_output_and_provider_error_are_not_empty_success():
    for invalid in (False, True):
        user = await account()
        pending = []

        async def provider(deps, invalid=invalid):
            if invalid:
                return LearningTutorOutput(), 1, 1
            raise RuntimeError("private provider content")

        runtime = LearningTutorGeneration(provider=provider, spawn=pending.append)
        async with db.async_session() as session:
            result = await runtime.admit(
                LearningTutorService(session, user),
                OperationRequest(
                    request_id=uuid.uuid4(),
                    revision=0,
                    epoch=0,
                    kind="turn",
                    content="Interest",
                ),
            )
        await pending.pop()
        async with db.async_session() as session:
            receipt = await LearningTutorService(session, user).operation(result.id)
            assert receipt.status == "failed"
            assert receipt.error_code == (
                "invalid_output" if invalid else "provider_failed"
            )
            row = await session.get(LearningTutorOperation, result.id)
            assert row is not None
            assert (row.input_tokens, row.output_tokens) == (
                (1, 1) if invalid else (0, 0)
            )


def draft(topic="New direction", evidence="focus", kind="path"):
    return dict(
        kind=kind,
        topic=topic,
        level="new_to_it",
        description="Learn",
        rationale="Matches your context",
        evidence_keys=[evidence],
    )


@pytest.mark.anyio
async def test_card_only_evidence_fences_prose_and_stale_dismissal_is_omitted():
    user = await account()
    pending, contexts = [], []
    started, release = asyncio.Event(), asyncio.Event()

    async def provider(deps):
        contexts.append(json.loads(deps.context))
        started.set()
        await release.wait()
        return LearningTutorOutput(reply="Advice based on previous cards"), 3, 4

    runtime = LearningTutorGeneration(provider=provider, spawn=pending.append)
    async with db.async_session() as session:
        source = Path(user_id=user.id, topic="Unsampled", level=Level.NEW_TO_IT)
        session.add(source)
        await session.commit()
        session.add_all(
            [
                Path(user_id=user.id, topic=f"Recent {i}", level=Level.NEW_TO_IT)
                for i in range(12)
            ]
        )
        excerpt = card(user.id, status="dismissed")
        excerpt.evidence = [{"kind": "path", "id": str(source.id)}]
        session.add(excerpt)
        obsolete = card(user.id, topic="Obsolete focus", status="dismissed")
        obsolete.focus_version = 99
        session.add(obsolete)
        await session.commit()
        request = OperationRequest(
            request_id=uuid.uuid4(),
            revision=0,
            epoch=0,
            kind="turn",
            content="Interest",
        )
        result = await runtime.admit(LearningTutorService(session, user), request)
    task = asyncio.create_task(pending.pop())
    await started.wait()
    async with db.async_session() as session:
        await session.execute(delete(Path).where(Path.id == source.id))
        await session.commit()
    release.set()
    await task
    assert all(p["topic"] != "Unsampled" for p in contexts[0]["paths"])
    assert [c["id"] for c in contexts[0]["recommendations"]] == [str(excerpt.id)]
    assert contexts[0]["recommendations"][0]["evidence"][0]["id"] == str(source.id)
    async with db.async_session() as session:
        receipt = await LearningTutorService(session, user).operation(result.id)
        assert receipt.status == "failed" and receipt.error_code == "context_changed"
        assert receipt.output is None
        await runtime.admit(
            LearningTutorService(session, user),
            request.model_copy(update={"request_id": uuid.uuid4()}),
        )
    await pending.pop()
    assert contexts[1]["recommendations"] == []


@pytest.mark.anyio
async def test_expired_receipt_retains_late_usage_without_resurrection():
    user = await account()
    pending = []
    runtime = LearningTutorGeneration(provider=FakeProvider(), spawn=pending.append)
    request = OperationRequest(
        request_id=uuid.uuid4(), revision=0, epoch=0, kind="turn", content="Interest"
    )
    async with db.async_session() as session:
        first = await runtime.admit(LearningTutorService(session, user), request)
        row = await session.get(LearningTutorOperation, first.id)
        assert row is not None
        row.expires_at = row.started_at - timedelta(seconds=1)
        row.input_tokens = 12
        await session.commit()
        await runtime.admit(
            LearningTutorService(session, user),
            request.model_copy(update={"request_id": uuid.uuid4()}),
        )
    await pending.pop(0)
    await pending.pop(0)
    async with db.async_session() as session:
        row = await session.get(LearningTutorOperation, first.id)
        assert row is not None
        assert (row.status, row.error_code, row.output) == (
            "failed",
            "interrupted",
            None,
        )
        assert (row.input_tokens, row.output_tokens) == (12, 9)


@pytest.mark.anyio
async def test_refresh_and_refinement_publish_cards_atomically_once():
    user = await account()
    pending = []
    response = LearningTutorOutput(
        recommendations=[draft("One"), draft("Two"), draft("Three")]
    )

    async def provider(deps):
        return response, 10, 20

    runtime = LearningTutorGeneration(provider=provider, spawn=pending.append)
    async with db.async_session() as session:
        service = LearningTutorService(session, user)
        await service.save_focus("Foundations", revision=0)
        result = await runtime.admit(
            service,
            OperationRequest(
                request_id=uuid.uuid4(), revision=1, epoch=0, kind="refresh"
            ),
        )
    await pending.pop()
    async with db.async_session() as session:
        service = LearningTutorService(session, user)
        state = await service.read()
        assert state.revision == 2 and len(state.recommendations) == 3
        assert (await service.operation(result.id)).status == "succeeded"
        selected = state.recommendations[0].id
        kept = {c.id for c in state.recommendations} - {selected}
        response = LearningTutorOutput(
            reply="Refined",
            replacement=selected,
            recommendations=[
                dict(
                    draft(state.recommendations[0].topic),
                    level="work_in_it",
                    guidance="Focus on practical applications.",
                )
            ],
        )
        await runtime.admit(
            service,
            OperationRequest(
                request_id=uuid.uuid4(),
                revision=2,
                epoch=0,
                kind="turn",
                content="Refine this",
                recommendation_id=selected,
            ),
        )
    await pending.pop()
    async with db.async_session() as session:
        state = await LearningTutorService(session, user).read()
        assert state.revision == 3 and len(state.recommendations) == 3
        assert kept < {c.id for c in state.recommendations}
        assert selected not in {c.id for c in state.recommendations}
        assert len(state.history.items) == 1
        refined = next(c for c in state.recommendations if c.id not in kept)
        assert refined.topic == response.recommendations[0].topic
        assert refined.level == "work_in_it"
        assert refined.guidance == "Focus on practical applications."


@pytest.mark.anyio
@pytest.mark.parametrize(
    "collision",
    ["none", "addition", "unselected", "other_active", "target", "dismissed"],
)
async def test_same_topic_refinement_preserves_exclusions(collision):
    user = await account()
    pending = []
    async with db.async_session() as session:
        selected = card(user.id)
        session.add(selected)
        if collision == "other_active":
            session.add(card(user.id, topic="Circuits"))
        elif collision == "target":
            session.add(
                Path(user_id=user.id, topic="ELECTRICITY", level=Level.NEW_TO_IT)
            )
        elif collision == "dismissed":
            session.add(card(user.id, status="dismissed"))
        await session.commit()
        selected_id = selected.id

    async def provider(deps):
        return (
            LearningTutorOutput(
                reply="Refined",
                replacement=None if collision == "addition" else selected_id,
                recommendations=[
                    dict(
                        draft(
                            "CIRCUITS"
                            if collision == "other_active"
                            else "ＥLECTRICITY",
                            "message",
                        ),
                        level="work_in_it",
                        guidance="Focus on circuit design.",
                    )
                ],
            ),
            1,
            2,
        )

    runtime = LearningTutorGeneration(provider=provider, spawn=pending.append)
    async with db.async_session() as session:
        result = await runtime.admit(
            LearningTutorService(session, user),
            OperationRequest(
                request_id=uuid.uuid4(),
                revision=0,
                epoch=0,
                kind="turn",
                content="Keep the Topic but make the Level and Guidance more advanced.",
                recommendation_id=None if collision == "unselected" else selected_id,
            ),
        )
    await pending.pop()
    async with db.async_session() as session:
        service = LearningTutorService(session, user)
        result = await service.operation(result.id)
        state = await service.read()
        if collision == "none":
            assert result.status == "succeeded"
            assert len(state.recommendations) == 1
            assert state.recommendations[0].id != selected_id
            assert state.recommendations[0].level == "work_in_it"
            assert state.recommendations[0].guidance == "Focus on circuit design."
            assert len(state.history.items) == 1
        else:
            assert (result.status, result.error_code) == ("failed", "invalid_output")
            assert state.revision == 0
            assert not state.history.items
            original = next(c for c in state.recommendations if c.id == selected_id)
            assert original.level == "new_to_it" and original.guidance is None
            assert len(state.recommendations) == (
                2 if collision == "other_active" else 1
            )


@pytest.mark.anyio
async def test_deleted_evidence_and_new_exact_topic_preserve_old_set():
    for change in ("delete", "duplicate"):
        user = await account()
        pending = []

        async def provider(deps):
            key = next(key for key in deps.evidence_keys if key.startswith("path:"))
            return (
                LearningTutorOutput(recommendations=[draft("New direction", key)]),
                1,
                1,
            )

        runtime = LearningTutorGeneration(provider=provider, spawn=pending.append)
        async with db.async_session() as session:
            path = Path(user_id=user.id, topic="Source", level=Level.NEW_TO_IT)
            previous = card(user.id)
            session.add_all([path, previous])
            await session.commit()
            result = await runtime.admit(
                LearningTutorService(session, user),
                OperationRequest(
                    request_id=uuid.uuid4(), revision=0, epoch=0, kind="refresh"
                ),
            )
        async with db.async_session() as session:
            if change == "delete":
                await session.execute(delete(Path).where(Path.id == path.id))
            else:
                session.add(
                    Path(
                        user_id=user.id,
                        topic=" NEW   DIRECTION ",
                        level=Level.NEW_TO_IT,
                    )
                )
            await session.commit()
        await pending.pop()
        async with db.async_session() as session:
            service = LearningTutorService(session, user)
            receipt = await service.operation(result.id)
            assert (
                receipt.status == "failed" and receipt.error_code == "context_changed"
            )
            state = await service.read()
            assert [c.id for c in state.recommendations] == [previous.id]
            assert state.revision == 0


@pytest.mark.anyio
async def test_dismissal_exclusion_beyond_prompt_window_and_context_budget():
    user = await account()
    pending, contexts = [], []

    async def provider(deps):
        contexts.append(deps)
        return LearningTutorOutput(reply="No new cards"), 1, 1

    runtime = LearningTutorGeneration(provider=provider, spawn=pending.append)
    async with db.async_session() as session:
        session.add_all(
            [
                card(user.id, topic=f"Dismissed {i}", status="dismissed")
                for i in range(30)
            ]
        )
        # Whole previous Turns consume the budget before sampled activity.
        for i in range(10):
            row = operation(user.id, status="succeeded", output={"reply": "r" * 6000})
            row.input = {"content": str(i) * 2000}
            session.add(row)
        await session.commit()
        await runtime.admit(
            LearningTutorService(session, user),
            OperationRequest(
                request_id=uuid.uuid4(),
                revision=0,
                epoch=0,
                kind="turn",
                content="Current interest",
            ),
        )
    await pending.pop()
    deps = contexts[0]
    assert len(deps.context) <= 40000
    context = json.loads(deps.context)
    assert context["content"] == "Current interest"
    assert 0 < len(context["turns"]) < 10
    assert all(
        len(turn["content"]) == 2000 and len(turn["output"]["reply"]) == 6000
        for turn in context["turns"]
    )
    assert context["omitted"]["turns"] == 10 - len(context["turns"])
    assert all(("path", f"dismissed {i}") in deps.excluded for i in range(30))


@pytest.mark.anyio
async def test_http_cold_validation_default_off_and_observational_interaction():
    disabled = await account(enabled=False, admin=True)
    user = await account()
    async with client(disabled) as http:
        assert (
            await http.post("/api/v1/learning-tutor/operations", json={})
        ).status_code == 404
    body = dict(request_id=str(uuid.uuid4()), revision=0, epoch=0, kind="refresh")
    async with client(user) as http:
        result = await http.post("/api/v1/learning-tutor/operations", json=body)
        assert (
            result.status_code == 200
            and result.json()["output"]["outcome"] == "needs_context"
        )
        assert (
            await http.post(
                "/api/v1/learning-tutor/operations",
                json={**body, "content": "forbidden"},
            )
        ).status_code == 422
        assert (await http.post("/api/v1/learning-tutor/operations", json=body)).json()[
            "id"
        ] == result.json()["id"]
        async with db.async_session() as session:
            row = card(user.id)
            session.add(row)
            await session.commit()
        interaction = await http.post(
            f"/api/v1/learning-tutor/recommendations/{row.id}/interactions",
            json=dict(
                interaction="explore", surface="home", session_id=str(uuid.uuid4())
            ),
        )
        assert interaction.status_code == 204
        state = (await http.get("/api/v1/learning-tutor/")).json()
        assert (
            state["revision"] == 1 and state["recommendations"][0]["status"] == "active"
        )


@pytest.mark.anyio
@pytest.mark.parametrize("invalid", [False, True])
async def test_real_stub_provider_publishes_grounded_card(invalid):
    user = await account()
    pending = []
    runtime = LearningTutorGeneration(
        config=Settings(model_learning_tutor="stub"), spawn=pending.append
    )
    async with db.async_session() as session:
        result = await runtime.admit(
            LearningTutorService(session, user),
            OperationRequest(
                request_id=uuid.uuid4(),
                revision=0,
                epoch=0,
                kind="turn",
                content="[lt-invalid]" if invalid else "[lt-path] Electricity",
            ),
        )
    await pending.pop()
    async with db.async_session() as session:
        service = LearningTutorService(session, user)
        receipt = await service.operation(result.id)
        row = await session.get(LearningTutorOperation, result.id)
        assert row is not None
        assert row.input_tokens > 0 and row.output_tokens > 0
        await session.rollback()
        if invalid:
            assert receipt.status == "failed" and receipt.error_code == "invalid_output"
            assert receipt.output is None
            return
        assert receipt.status == "succeeded"
        state = await service.read()
        assert len(state.recommendations) == 1
        assert state.recommendations[0].evidence is not None
        assert state.recommendations[0].evidence[0].kind == "message"
        assert state.revision == 1


@pytest.mark.anyio
async def test_provider_holds_no_connection_and_permit_wait_times_out(
    isolated_database,
):
    user = await account()
    engine = create_async_engine(isolated_database, pool_size=1, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    started, release = asyncio.Event(), asyncio.Event()

    async def provider(deps):
        started.set()
        await release.wait()
        return LearningTutorOutput(reply="Done"), 1, 1

    pending = []
    runtime = LearningTutorGeneration(
        provider=provider,
        spawn=pending.append,
        session_factory=sessions,
        config=Settings(learning_tutor_timeout=1),
    )
    try:
        async with sessions() as session:
            result = await runtime.admit(
                LearningTutorService(session, user),
                OperationRequest(
                    request_id=uuid.uuid4(),
                    revision=0,
                    epoch=0,
                    kind="turn",
                    content="Interest",
                ),
            )
        task = asyncio.create_task(pending.pop())
        await started.wait()
        # Acquiring the only connection during provider execution must work.
        async with asyncio.timeout(0.5):
            async with sessions() as session:
                receipt = await LearningTutorService(session, user).operation(result.id)
                assert receipt.status == "running"
        release.set()
        await task
        runtime.semaphore = asyncio.Semaphore(0)
        async with sessions() as session:
            result = await runtime.admit(
                LearningTutorService(session, user),
                OperationRequest(
                    request_id=uuid.uuid4(),
                    revision=1,
                    epoch=0,
                    kind="turn",
                    content="Again",
                ),
            )
        await pending.pop()
        async with sessions() as session:
            receipt = await LearningTutorService(session, user).operation(result.id)
            assert receipt.status == "failed" and receipt.error_code == "timed_out"
    finally:
        await engine.dispose()
