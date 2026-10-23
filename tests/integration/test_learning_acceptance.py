"""Acceptance uses actual committed PostgreSQL transactions, never a shared session."""

import asyncio
from uuid import UUID

import pytest
from sqlalchemy import delete, func, select

from aleph import db, events
from aleph.config import Settings, settings
from aleph.models import Beat, Level, Path, UserFeatureOverride
from aleph.models.learning_tutor import LearningRecommendation
from aleph.repositories import UserRepository
from aleph.services.briefing import briefing_service
from aleph.services.generation import generation_orchestrator
from aleph.services.scheduled_beats import run_beats

from .conftest import CollectingSpawn, stub_resolver, wait_until_lock_waiters
from .test_learning_tutor import URL, account, card, client
from .test_scheduled_beats import skip_service


class LostDispatch:
    """Record scheduling without ever running provider work."""

    def __init__(self):
        self.pending = []

    def __call__(self, coroutine):
        self.pending.append(coroutine)
        coroutine.close()


@pytest.fixture
def dispatch(monkeypatch):
    spawn = LostDispatch()
    monkeypatch.setattr(generation_orchestrator, "_spawn", spawn)
    monkeypatch.setattr(briefing_service, "_spawn", spawn)
    return spawn


async def setup(kind):
    user = await account()
    async with db.async_session() as session:
        row = card(user.id)
        row.kind = kind
        session.add(row)
        await session.commit()
    body = {
        "topic": " Edited topic ",
        "level": "some_experience",
        "guidance": " My guidance ",
        "learning_recommendation_id": str(row.id),
        "learning_tutor_revision": 0,
    }
    if kind == "beat":
        body["anchor_weekday"] = 3
    else:
        body["teaching_style"] = " Examples first "
    return user, row.id, body


async def race(user, endpoint, first_body, second_body):
    async with db.async_session() as blocker:
        await UserRepository(blocker).lock_for_creation(user.id)
        async with client(user) as first, client(user) as second:
            a = asyncio.create_task(first.post(endpoint, json=first_body))
            b = asyncio.create_task(second.post(endpoint, json=second_body))
            await asyncio.wait_for(wait_until_lock_waiters(2), 5)
            await blocker.commit()
            return await asyncio.gather(a, b)


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["path", "beat"])
async def test_accept_replay_clear_deleted_target(kind, dispatch, monkeypatch):
    user, card_id, body = await setup(kind)
    monkeypatch.setattr(
        settings,
        "rate_limit_paths_per_day" if kind == "path" else "max_beats_per_learner",
        1,
    )
    endpoint = f"/api/v1/{kind}s"
    async with client(user) as http:
        response = await http.post(endpoint, json=body)
        assert response.status_code == 202, response.text
        target_id = response.json()["id"]
        state = (await http.get(URL + "/")).json()
        assert state["revision"] == 1
        assert state["recommendations"] == []
        assert len(dispatch.pending) == 1
        # Lost response: replay with normalized text and stale revision.
        body["topic"] = "Edited topic"
        replay = await http.post(endpoint, json=body)
        assert replay.status_code == 202 and replay.json()["id"] == target_id
        assert len(dispatch.pending) == 1
        changed = await http.post(endpoint, json={**body, "topic": "Different"})
        assert changed.status_code == 409
        assert (await http.delete(URL + "/conversation?revision=1")).status_code == 200
        assert (await http.post(endpoint, json=body)).json()["id"] == target_id
        assert (await http.delete(endpoint + "/" + target_id)).status_code == 204
        gone = await http.post(endpoint, json=body)
        assert gone.status_code == 410
        assert gone.json()["error"]["details"]["reason"] == "accepted_target_deleted"
    async with db.async_session() as session:
        receipt = await session.get(LearningRecommendation, card_id)
        assert receipt is not None
        assert receipt.status == "accepted" and receipt.topic is None


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["path", "beat"])
async def test_concurrent_accept_and_ordinary_cap(kind, dispatch, monkeypatch):
    user, _, body = await setup(kind)
    endpoint = f"/api/v1/{kind}s"
    # Actual concurrent HTTP requests each own their own DB session/commit.
    a, b = await race(user, endpoint, body, body)
    assert a.status_code == b.status_code == 202
    assert a.json()["id"] == b.json()["id"]
    assert len(dispatch.pending) == 1
    async with db.async_session() as session:
        model = Path if kind == "path" else Beat
        assert await session.scalar(select(func.count()).select_from(model)) == 1

    # A new account races ordinary against recommended for its final slot.
    user, _, body = await setup(kind)
    monkeypatch.setattr(
        settings,
        "rate_limit_paths_per_day" if kind == "path" else "max_beats_per_learner",
        1,
    )
    ordinary = {k: v for k, v in body.items() if not k.startswith("learning_")}
    a, b = await race(user, endpoint, body, ordinary)
    assert sorted([a.status_code, b.status_code]) == [202, 429]


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["path", "beat"])
async def test_atomic_rollback_before_receipt(kind, dispatch, monkeypatch):
    user, card_id, body = await setup(kind)
    from aleph.services.creation import accept_created

    async def fail(*args, **kwargs):
        raise RuntimeError("injected after insertion")

    monkeypatch.setattr("aleph.services.creation.accept_created", fail)
    async with client(user) as http:
        with pytest.raises(RuntimeError, match="injected"):
            await http.post(f"/api/v1/{kind}s", json=body)
    async with db.async_session() as session:
        model = Path if kind == "path" else Beat
        assert await session.scalar(select(func.count()).select_from(model)) == 0
        receipt = await session.get(LearningRecommendation, card_id)
        assert receipt is not None and receipt.status == "active"
    assert not dispatch.pending
    monkeypatch.setattr("aleph.services.creation.accept_created", accept_created)


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["path", "beat"])
async def test_pair_validation_and_feature_gate(kind, dispatch):
    user, _, body = await setup(kind)
    async with client(user) as http:
        missing = dict(body)
        del missing["learning_tutor_revision"]
        assert (await http.post(f"/api/v1/{kind}s", json=missing)).status_code == 422
    disabled = await account(enabled=False)
    async with client(disabled) as http:
        assert (await http.post(f"/api/v1/{kind}s", json=body)).status_code == 404
    assert not dispatch.pending


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["path", "beat"])
@pytest.mark.parametrize(
    "failure", ["stale", "dismissed", "kind", "unowned", "focus", "source", "flag"]
)
async def test_acceptance_rejects_unavailable(kind, failure, dispatch):
    user, card_id, body = await setup(kind)
    async with db.async_session() as session:
        row = await session.get(LearningRecommendation, card_id)
        assert row is not None
        if failure == "stale":
            body["learning_tutor_revision"] = 7
        elif failure == "dismissed":
            row.status = "dismissed"
        elif failure == "kind":
            row.kind = "beat" if kind == "path" else "path"
        elif failure == "focus":
            row.focus_version = 1
        elif failure == "source":
            import uuid

            row.evidence = [{"kind": "path", "id": str(uuid.uuid4())}]
        elif failure == "flag":
            await session.execute(
                delete(UserFeatureOverride).where(
                    UserFeatureOverride.user_id == user.id
                )
            )
        await session.commit()
    if failure == "unowned":
        user = await account()
    async with client(user) as http:
        response = await http.post(f"/api/v1/{kind}s", json=body)
    assert response.status_code == (404 if failure in {"unowned", "flag"} else 409)
    assert not dispatch.pending


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["path", "beat"])
async def test_dispatch_failure_returns_committed_target_and_retry(
    kind, dispatch, monkeypatch
):
    user, _, body = await setup(kind)

    def fail(coroutine):
        raise RuntimeError("dispatch unavailable")

    owner = generation_orchestrator if kind == "path" else briefing_service
    monkeypatch.setattr(owner, "_spawn", fail)
    async with client(user) as http:
        endpoint = f"/api/v1/{kind}s"
        result = await http.post(endpoint, json=body)
        assert result.status_code == 202, result.text
        target_id = result.json()["id"]
        monkeypatch.setattr(owner, "_spawn", dispatch)
        detail = (await http.get(endpoint + "/" + target_id)).json()
        assert detail["status" if kind == "path" else "research_state"] == "failed"
        replay = await http.post(endpoint, json=body)
        assert replay.status_code == 202 and replay.json()["id"] == target_id
        before = len(dispatch.pending)
        assert (
            await http.post(endpoint + "/" + target_id + "/retry")
        ).status_code == 202
        assert len(dispatch.pending) > before


@pytest.mark.anyio
async def test_commit_before_path_dispatch_recovers_via_poll(dispatch, monkeypatch):
    user, _, body = await setup("path")
    async with client(user) as http:
        result = await http.post("/api/v1/paths", json=body)
        target_id = result.json()["id"]
        spawn = CollectingSpawn(hold=True)
        monkeypatch.setattr(generation_orchestrator, "_spawn", spawn)
        monkeypatch.setattr(generation_orchestrator, "_resolve_model", stub_resolver())
        try:
            assert (await http.get("/api/v1/paths/" + target_id)).status_code == 200
            await spawn.drain()
            async with db.async_session() as session:
                path = await session.get(Path, UUID(target_id))
                assert path is not None and path.status.value == "ready"
        finally:
            await spawn.cancel_pending()


@pytest.mark.anyio
async def test_commit_before_beat_dispatch_recovers_via_job(dispatch, monkeypatch):
    user, _, body = await setup("beat")

    async def lost(*args, **kwargs):
        pass  # process dies after creation commit, before even claiming research

    monkeypatch.setattr(briefing_service, "trigger_initial", lost)
    async with client(user) as http:
        result = await http.post("/api/v1/beats", json=body)
        assert result.status_code == 202
        assert result.json()["research_state"] == "idle"
    outcome = await run_beats(skip_service(), config=Settings())
    assert outcome["claimed"] == outcome["skipped"] == 1
    async with db.async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Beat)) == 1


@pytest.mark.anyio
async def test_source_delete_wins_before_acceptance(dispatch):
    user, card_id, body = await setup("path")
    async with db.async_session() as session:
        source = Path(user_id=user.id, topic="Source", level=Level.NEW_TO_IT)
        session.add(source)
        await session.flush()
        row = await session.get(LearningRecommendation, card_id)
        assert row is not None
        row.evidence = [{"kind": "path", "id": str(source.id)}]
        await session.commit()
    async with db.async_session() as deleting, client(user) as http:
        await deleting.execute(delete(Path).where(Path.id == source.id))
        accepting = asyncio.create_task(http.post("/api/v1/paths", json=body))
        await asyncio.wait_for(wait_until_lock_waiters(1), 5)
        await deleting.commit()
        assert (await accepting).status_code == 409
    assert not dispatch.pending


@pytest.mark.anyio
async def test_acceptance_locks_source_until_commit(dispatch, monkeypatch):
    from aleph.services import creation

    user, card_id, body = await setup("path")
    async with db.async_session() as session:
        source = Path(user_id=user.id, topic="Source", level=Level.NEW_TO_IT)
        session.add(source)
        await session.flush()
        row = await session.get(LearningRecommendation, card_id)
        assert row is not None
        row.evidence = [{"kind": "path", "id": str(source.id)}]
        await session.commit()
    inserted, proceed = asyncio.Event(), asyncio.Event()
    real_accept = creation.accept_created

    async def pause(*args):
        inserted.set()
        await proceed.wait()
        await real_accept(*args)

    async def delete_source():
        async with db.async_session() as session:
            await session.execute(delete(Path).where(Path.id == source.id))
            await session.commit()

    monkeypatch.setattr(creation, "accept_created", pause)
    async with client(user) as http:
        accepting = asyncio.create_task(http.post("/api/v1/paths", json=body))
        await asyncio.wait_for(inserted.wait(), 5)
        deleting = asyncio.create_task(delete_source())
        await asyncio.wait_for(wait_until_lock_waiters(1), 5)
        proceed.set()
        assert (await accepting).status_code == 202
        await deleting


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["path", "beat"])
async def test_acceptance_event_is_postcommit_once(kind, dispatch, monkeypatch):
    user, card_id, body = await setup(kind)
    emitted = []
    monkeypatch.setattr(
        events, "_emit", lambda name, **fields: emitted.append((name, fields))
    )
    async with client(user) as http:
        endpoint = f"/api/v1/{kind}s"
        result = await http.post(endpoint, json=body)
        assert result.status_code == 202
        assert (await http.post(endpoint, json=body)).status_code == 202
    accepts = [
        fields
        for name, fields in emitted
        if name == events.LEARNING_RECOMMENDATION_ACCEPTED
    ]
    assert accepts == [
        {
            "account_id": str(user.id),
            "recommendation_id": str(card_id),
            "target_kind": kind,
            "target_id": result.json()["id"],
            "workflow": "learning_tutor",
        }
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", ["clear", "disable"])
async def test_waiting_acceptance_rechecks_clear_or_flag(mutation, dispatch):
    from aleph.repositories.learning_tutor import LearningTutorRepository
    from aleph.services.feature_flags import FeatureFlagService

    user, _, body = await setup("path")
    async with db.async_session() as changing, client(user) as http:
        # Disable owns users; clear owns state. Both are independent committed
        # transactions, with pg_stat_activity proving the request is waiting.
        if mutation == "disable":
            await UserRepository(changing).lock_by_id(user.id)
        else:
            await LearningTutorRepository(changing).lock_state(user.id)
        accepting = asyncio.create_task(http.post("/api/v1/paths", json=body))
        await asyncio.wait_for(wait_until_lock_waiters(1), 5)
        if mutation == "disable":
            await FeatureFlagService(changing).set_user_override(
                flag_key="learning_tutor", user_id=user.id, enabled=False
            )
        else:
            repo = LearningTutorRepository(changing)
            state = await repo.lock_state(user.id)
            await repo.clear(user.id, await repo.now())
            state.epoch += 1
            state.revision += 1
            await changing.commit()
        assert (await accepting).status_code == 404
    assert not dispatch.pending


@pytest.mark.anyio
async def test_state_writer_fk_insert_does_not_deadlock_acceptance(dispatch):
    from aleph.repositories.learning_tutor import LearningTutorRepository

    from .test_learning_tutor import operation

    user, _, body = await setup("path")
    async with db.async_session() as writer, client(user) as http:
        await LearningTutorRepository(writer).lock_state(user.id)
        accepting = asyncio.create_task(http.post("/api/v1/paths", json=body))
        await asyncio.wait_for(wait_until_lock_waiters(1), 5)
        writer.add(operation(user.id))
        await asyncio.wait_for(writer.commit(), 5)
        assert (await accepting).status_code == 202
