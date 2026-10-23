"""Learning Tutor state, ownership, receipts and concurrent database writers."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aleph import db
from aleph.app import create_app
from aleph.dependencies import get_current_user
from aleph.domains.learning_recommendations import RecommendationConflict
from aleph.dtos.learning_tutor import EvidenceReference, RecommendationDraft
from aleph.models import Level, Path, User, UserFeatureOverride
from aleph.models.learning_tutor import (
    LearningRecommendation,
    LearningTutorOperation,
    LearningTutorState,
)
from aleph.repositories.learning_tutor import LearningTutorRepository
from aleph.services.learning_tutor import LearningTutorService

from .conftest import create_user, run_alembic, wait_until_lock_waiters

URL = "/api/v1/learning-tutor"


def test_upgrade_from_previous_head_preserves_existing_path(isolated_database):
    run_alembic(isolated_database, "0021_lesson_conversations", downgrade=True)

    async def seed():
        async with db.async_session() as session:
            user = await create_user(session)
            path = Path(user_id=user.id, topic="Preserved", level=Level.NEW_TO_IT)
            session.add(path)
            await session.commit()
            return path.id

    path_id = asyncio.run(seed())
    asyncio.run(db.dispose_engine())
    run_alembic(isolated_database, "head")

    async def verify():
        async with db.async_session() as session:
            path = await session.get(Path, path_id)
            assert path is not None and path.topic == "Preserved"
            assert (
                await session.scalar(
                    select(func.count()).select_from(LearningTutorState)
                )
                == 0
            )

    asyncio.run(verify())


async def account(*, enabled=True, admin=False):
    async with db.async_session() as session:
        user = await create_user(
            session,
            username=uuid.uuid4().hex,
            email="admin@mattjmcnaughton.com" if admin else "test@example.com",
        )
        if enabled:
            session.add(
                UserFeatureOverride(
                    user_id=user.id, flag_key="learning_tutor", enabled=True
                )
            )
        await session.commit()
        return user


def client(user):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def card(user_id, *, topic="Electricity", status="active", **kwargs):
    return LearningRecommendation(
        user_id=user_id,
        epoch=0,
        focus_version=0,
        kind="path",
        topic=topic,
        normalized_topic=topic.casefold(),
        level="new_to_it",
        description="Learn it",
        rationale="You asked",
        evidence=[{"kind": "message"}],
        status=status,
        **kwargs,
    )


def operation(user_id, *, status="running", **kwargs):
    now = datetime.now(UTC)
    return LearningTutorOperation(
        user_id=user_id,
        epoch=0,
        request_id=uuid.uuid4(),
        request_hash="hash",
        kind="turn",
        revision=0,
        status=status,
        started_at=now,
        expires_at=now + timedelta(seconds=120),
        input={"content": "Interest"},
        model_admitted=True,
        **kwargs,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("admin", [False, True])
async def test_dark_gate_and_empty_read_only_state(admin):
    user = await account(enabled=False, admin=admin)
    async with client(user) as http:
        assert (await http.get(URL + "/")).status_code == 404
    async with db.async_session() as session:
        session.add(
            UserFeatureOverride(
                user_id=user.id, flag_key="learning_tutor", enabled=True
            )
        )
        await session.commit()
    async with client(user) as http:
        response = await http.get(URL + "/")
        assert response.status_code == 200
        state = response.json()
        assert (state["focus"], state["revision"], state["epoch"]) == (None, 0, 0)
        assert state["recommendations"] == state["history"]["items"] == []
        # LT-2 exposes explicit admission; invalid input still allocates no state.
        assert (await http.post(URL + "/operations", json={})).status_code == 422
    async with db.async_session() as session:
        assert (
            await session.scalar(select(func.count()).select_from(LearningTutorState))
            == 0
        )


@pytest.mark.anyio
async def test_focus_cas_and_clear_fences_and_redacts():
    user = await account()
    async with client(user) as http:
        saved = await http.put(
            URL + "/focus", json={"focus": "  Power markets  ", "revision": 0}
        )
        assert saved.status_code == 200
        assert saved.json()["focus"] == "Power markets"
        stale = await http.put(
            URL + "/focus", json={"focus": "Lost edit", "revision": 0}
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["details"]["reason"] == "stale_state"
    async with db.async_session() as session:
        target = Path(user_id=user.id, topic="Existing", level=Level.NEW_TO_IT)
        session.add(target)
        await session.flush()
        accepted = card(
            user.id,
            status="accepted",
            accepted_path_id=target.id,
            accepted_at=datetime.now(UTC),
            accepted_request_hash="accepted-hash",
        )
        pending = operation(user.id)
        session.add_all(
            [accepted, pending, card(user.id, topic="Dismissed", status="dismissed")]
        )
        await session.commit()
        accepted_id, pending_id = accepted.id, pending.id
    async with client(user) as http:
        cleared = await http.delete(URL + "/conversation?revision=1")
        assert cleared.status_code == 200
        assert (
            cleared.json()["epoch"],
            cleared.json()["revision"],
            cleared.json()["focus"],
        ) == (1, 2, "Power markets")
        receipt = (await http.get(URL + f"/operations/{pending_id}")).json()
        assert receipt["status"] == "failed"
        assert receipt["input"] is None and receipt["output"] is None
        blank = await http.put(URL + "/focus", json={"focus": " \n ", "revision": 2})
        assert blank.json()["focus"] is None
    async with db.async_session() as session:
        retained = await session.get(LearningRecommendation, accepted_id)
        assert retained is not None
        assert retained.topic is None and retained.evidence is None
        assert retained.accepted_request_hash == "accepted-hash"
        assert retained.accepted_path_id == target.id
        assert (
            await session.scalar(
                select(func.count()).select_from(LearningRecommendation)
            )
            == 1
        )
        pending_receipt = await session.get(LearningTutorOperation, pending_id)
        assert pending_receipt is not None and pending_receipt.model_admitted


@pytest.mark.anyio
async def test_decisions_capacity_staleness_and_ownership():
    user, other = await account(), await account()
    async with db.async_session() as session:
        cards = [card(user.id, topic=str(n)) for n in range(3)]
        dismissed = card(user.id, topic="Fourth", status="dismissed")
        foreign = card(other.id)
        session.add_all([*cards, dismissed, foreign])
        await session.commit()
    async with client(user) as http:
        assert (
            await http.get(URL + f"/recommendations/{foreign.id}")
        ).status_code == 404
        assert (
            await http.put(
                URL + f"/recommendations/{foreign.id}/decision",
                json={"decision": "dismiss", "revision": 0},
            )
        ).status_code == 404
        full = await http.put(
            URL + f"/recommendations/{dismissed.id}/decision",
            json={"decision": "restore", "revision": 0},
        )
        assert full.status_code == 409
        assert full.json()["error"]["details"]["reason"] == "recommendation_capacity"
        assert (
            await http.put(
                URL + f"/recommendations/{cards[0].id}/decision",
                json={"decision": "dismiss", "revision": 0},
            )
        ).status_code == 200
        restored = await http.put(
            URL + f"/recommendations/{dismissed.id}/decision",
            json={"decision": "restore", "revision": 1},
        )
        assert restored.status_code == 200
        assert len(restored.json()["recommendations"]) == 3
        assert (
            await http.put(URL + "/focus", json={"focus": "New focus", "revision": 2})
        ).json()["recommendations"] == []
        assert (
            await http.get(URL + f"/recommendations/{dismissed.id}")
        ).status_code == 409


@pytest.mark.anyio
async def test_two_sessions_compete_for_first_state_write():
    user = await account()
    async with db.async_session() as first:
        await LearningTutorRepository(first).lock_state(user.id)

        async def competing():
            async with db.async_session() as second:
                return await LearningTutorService(second, user).save_focus(
                    "loser", revision=0
                )

        task = asyncio.create_task(competing())
        await asyncio.wait_for(wait_until_lock_waiters(1), 5)
        await LearningTutorService(first, user).save_focus("winner", revision=0)
        with pytest.raises(RecommendationConflict, match="stale_state"):
            await asyncio.wait_for(task, 5)
    async with client(user) as http:
        assert (await http.get(URL + "/")).json()["focus"] == "winner"


@pytest.mark.anyio
async def test_database_duplicates_running_receipts_and_account_cascade():
    user = await account()
    async with db.async_session() as session:
        first = card(user.id)
        run = operation(user.id)
        session.add_all([first, run])
        await session.flush()
        state = await LearningTutorRepository(session).lock_state(user.id)
        state.last_refresh_operation_id = run.id
        await session.commit()
        for duplicate in [card(user.id), operation(user.id)]:
            async with session.begin_nested():
                session.add(duplicate)
                with pytest.raises(IntegrityError):
                    await session.flush()
        await session.rollback()
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()
        for model in (
            LearningRecommendation,
            LearningTutorOperation,
            LearningTutorState,
        ):
            assert await session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.anyio
async def test_target_deletion_preserves_acceptance_and_rejects_cross_owner_fk():
    user, other = await account(), await account()
    async with db.async_session() as session:
        target = Path(user_id=user.id, topic="Target", level=Level.NEW_TO_IT)
        session.add(target)
        await session.flush()
        accepted = card(
            user.id,
            status="accepted",
            accepted_path_id=target.id,
            accepted_at=datetime.now(UTC),
            accepted_request_hash="hash",
        )
        session.add(accepted)
        await session.commit()
        target_id, accepted_id = target.id, accepted.id
        async with session.begin_nested():
            session.add(
                card(
                    other.id,
                    status="accepted",
                    accepted_path_id=target_id,
                    accepted_at=datetime.now(UTC),
                    accepted_request_hash="hash",
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()
        await session.execute(delete(Path).where(Path.id == target_id))
        await session.commit()
        session.expire_all()
        receipt = await session.get(LearningRecommendation, accepted_id)
        assert receipt is not None
        assert receipt.status == "accepted" and receipt.accepted_path_id is None
    async with client(user) as http:
        assert (await http.get(URL + f"/recommendations/{accepted_id}")).json()[
            "target_available"
        ] is False


@pytest.mark.anyio
async def test_history_pagination_whole_turns_current_epoch_and_expiry():
    user, other = await account(), await account()
    async with db.async_session() as session:
        turns = [
            operation(user.id, status="succeeded", output={"reply": str(n)})
            for n in range(23)
        ]
        failed = operation(user.id, status="failed")
        refresh = operation(user.id, status="succeeded", output={"recommendations": []})
        refresh.kind = "refresh"
        expired = operation(user.id)
        expired.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        foreign = operation(other.id, status="succeeded", output={"reply": "private"})
        session.add_all([*turns, failed, refresh, expired, foreign])
        await session.commit()
        expired_id, foreign_id = expired.id, foreign.id
    async with client(user) as http:
        page = (await http.get(URL + "/history")).json()
        assert len(page["items"]) == 20
        assert all(
            item["input"]["content"] == "Interest" and item["output"]["reply"]
            for item in page["items"]
        )
        older = (await http.get(URL + f"/history?before={page['before']}")).json()
        assert len(older["items"]) == 3 and older["before"] is None
        ids = [item["id"] for item in page["items"] + older["items"]]
        assert len(set(ids)) == 23
        assert (await http.get(URL + f"/operations/{foreign_id}")).status_code == 404
        assert (
            await http.get(URL + f"/history?before={foreign_id}")
        ).status_code == 404
        receipt = (await http.get(URL + f"/operations/{expired_id}")).json()
        assert (receipt["status"], receipt["error_code"]) == ("failed", "interrupted")
        assert (await http.get(URL + "/")).json()["current_operation_id"] is None
        await http.delete(URL + "/conversation?revision=0")
        assert (await http.get(URL + "/history")).json()["items"] == []
        assert (
            await http.get(URL + f"/history?before={page['before']}")
        ).status_code == 404


@pytest.mark.anyio
async def test_deleted_sources_hidden_without_get_writes_and_do_not_hold_capacity():
    user = await account()
    async with db.async_session() as session:
        source = Path(user_id=user.id, topic="Source", level=Level.NEW_TO_IT)
        session.add(source)
        await session.flush()
        stale = card(user.id)
        stale.evidence = [{"kind": "path", "id": str(source.id)}]
        restore = card(user.id, status="dismissed")
        session.add_all(
            [stale, restore, card(user.id, topic="Two"), card(user.id, topic="Three")]
        )
        await session.commit()
        stale_id = stale.id
        await session.execute(delete(Path).where(Path.id == source.id))
        await session.commit()
    async with client(user) as http:
        assert len((await http.get(URL + "/")).json()["recommendations"]) == 2
        assert (await http.get(URL + f"/recommendations/{stale_id}")).status_code == 409
    async with db.async_session() as session:
        saved = await session.get(LearningRecommendation, stale_id)
        assert saved is not None and saved.status == "active"
    async with client(user) as http:
        response = await http.put(
            URL + f"/recommendations/{restore.id}/decision",
            json={"decision": "restore", "revision": 0},
        )
        assert response.status_code == 200
        assert len(response.json()["recommendations"]) == 3
    async with db.async_session() as session:
        saved = await session.get(LearningRecommendation, stale_id)
        assert saved is not None and saved.status == "superseded"


@pytest.mark.anyio
async def test_restore_rejects_normalized_existing_target():
    user = await account()
    async with db.async_session() as session:
        dismissed = card(user.id, status="dismissed", topic="strasse")
        session.add_all(
            [
                dismissed,
                Path(
                    user_id=user.id, topic="  ＳＴＲＡＳＳＥ  ", level=Level.NEW_TO_IT
                ),
            ]
        )
        await session.commit()
    async with client(user) as http:
        assert (
            await http.put(
                URL + f"/recommendations/{dismissed.id}/decision",
                json={"decision": "restore", "revision": 0},
            )
        ).status_code == 409


@pytest.mark.anyio
async def test_publication_seam_exact_dismissals_kind_and_cap():
    user = await account()
    async with db.async_session() as session:
        service = LearningTutorService(session, user)
        draft = RecommendationDraft(
            kind="path",
            topic="  Straße  ",
            level="new_to_it",
            description="A direction",
            rationale="An interest",
            evidence=[EvidenceReference(kind="focus")],
        )
        first = await service.add_recommendation(draft)
        assert first.normalized_topic == "strasse"
        with pytest.raises(RecommendationConflict, match="recommendation_unavailable"):
            await service.add_recommendation(
                draft.model_copy(update={"topic": "ＳＴＲＡＳＳＥ"})
            )
        first.status = "dismissed"
        await session.flush()
        with pytest.raises(RecommendationConflict, match="recommendation_unavailable"):
            await service.add_recommendation(draft)
        beat = await service.add_recommendation(
            draft.model_copy(update={"kind": "beat"})
        )
        assert beat.kind == "beat"
        for topic in ("Two", "Three"):
            await service.add_recommendation(draft.model_copy(update={"topic": topic}))
        with pytest.raises(RecommendationConflict, match="recommendation_capacity"):
            await service.add_recommendation(draft.model_copy(update={"topic": "Four"}))
        await session.commit()


@pytest.mark.anyio
async def test_two_session_publication_cannot_overfill_card_cap():
    user = await account()
    async with db.async_session() as session:
        session.add_all([card(user.id, topic="One"), card(user.id, topic="Two")])
        await session.commit()
    draft = RecommendationDraft(
        kind="path",
        topic="Third",
        level="new_to_it",
        description="Direction",
        rationale="Interest",
        evidence=[EvidenceReference(kind="message")],
    )
    async with db.async_session() as first:
        await LearningTutorService(first, user).add_recommendation(draft)

        async def competing():
            async with db.async_session() as second:
                await LearningTutorService(second, user).add_recommendation(
                    draft.model_copy(update={"topic": "Fourth"})
                )
                await second.commit()

        task = asyncio.create_task(competing())
        await asyncio.wait_for(wait_until_lock_waiters(1), 5)
        await first.commit()
        with pytest.raises(RecommendationConflict, match="recommendation_capacity"):
            await asyncio.wait_for(task, 5)
    async with client(user) as http:
        assert len((await http.get(URL + "/")).json()["recommendations"]) == 3


@pytest.mark.anyio
async def test_operation_owner_fk_replay_identity_and_pruning_nulls_references():
    user, other = await account(), await account()
    async with db.async_session() as session:
        run = operation(user.id, status="failed")
        session.add(run)
        await session.flush()
        saved = card(user.id, operation_id=run.id)
        state = await LearningTutorRepository(session).lock_state(user.id)
        state.last_refresh_operation_id = run.id
        session.add(saved)
        await session.commit()
        run_id, request_id, saved_id = run.id, run.request_id, saved.id
        replay = await LearningTutorRepository(session).replay(user.id, 0, request_id)
        assert replay is not None and replay.id == run_id
        assert (
            await LearningTutorRepository(session).replay(other.id, 0, request_id)
            is None
        )
        for invalid in [
            card(other.id, operation_id=run_id),
            operation(user.id, status="failed"),
        ]:
            if isinstance(invalid, LearningTutorOperation):
                invalid.request_id = request_id
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    session.add(invalid)
                    await session.flush()
        await session.execute(
            delete(LearningTutorOperation).where(LearningTutorOperation.id == run_id)
        )
        await session.commit()
        session.expire_all()
        saved = await session.get(LearningRecommendation, saved_id)
        state = await session.get(LearningTutorState, user.id)
        assert saved is not None and saved.operation_id is None
        assert state is not None and state.last_refresh_operation_id is None


@pytest.mark.anyio
@pytest.mark.parametrize("invalid", ["kind", "payload", "accepted", "target_kind"])
async def test_database_rejects_invalid_recommendations(invalid):
    user = await account()
    async with db.async_session() as session:
        row = card(user.id)
        if invalid == "kind":
            row.kind = "other"
        elif invalid == "payload":
            row.topic = None
        elif invalid == "accepted":
            row.status = "accepted"
        else:
            target = Path(user_id=user.id, topic="Target", level=Level.NEW_TO_IT)
            session.add(target)
            await session.flush()
            row.kind = "beat"
            row.status = "accepted"
            row.accepted_path_id = target.id
            row.accepted_at = datetime.now(UTC)
            row.accepted_request_hash = "hash"
        session.add(row)
        with pytest.raises(IntegrityError):
            await session.flush()


@pytest.mark.anyio
async def test_analyst_gate_and_input_validation(monkeypatch):
    from aleph.config import settings

    user = await account()
    async with db.async_session() as session:
        beat = card(user.id)
        beat.kind = "beat"
        session.add(beat)
        await session.commit()
    monkeypatch.setattr(settings, "feature_flag_defaults", "analyst:off")
    async with client(user) as http:
        assert (await http.get(URL + "/")).json()["recommendations"] == []
        assert (await http.get(URL + f"/recommendations/{beat.id}")).status_code == 404
        assert (
            await http.put(
                URL + f"/recommendations/{beat.id}/decision",
                json={"decision": "dismiss", "revision": 0},
            )
        ).status_code == 404
        for payload in (
            {"focus": "x" * 2001, "revision": 0},
            {"focus": "x", "revision": -1},
            {"focus": "x", "revision": 0, "epoch": 1},
        ):
            assert (await http.put(URL + "/focus", json=payload)).status_code == 422
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        assert (await http.get(URL + "/")).status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize("existing", [False, True])
async def test_state_read_is_one_snapshot_during_committed_replacement(
    monkeypatch, existing
):
    user = await account()
    if existing:
        async with db.async_session() as session:
            state = await LearningTutorRepository(session).lock_state(user.id)
            state.focus = "Old focus"
            session.add_all(
                [
                    card(user.id),
                    operation(
                        user.id, status="succeeded", output={"reply": "Old turn"}
                    ),
                ]
            )
            await session.commit()

    original_state = LearningTutorRepository.state
    replaced = False

    async def read_then_replace(repository, user_id):
        nonlocal replaced
        row = await original_state(repository, user_id)
        if not replaced:
            replaced = True
            # The reader has observed its state. A different real transaction
            # clears and publishes a new epoch before the reader loads cards.
            # Awaiting commit proves the reader is not blocking the writer.
            async with db.async_session() as writer:
                repo = LearningTutorRepository(writer)
                state = await repo.lock_state(user_id)
                await repo.clear(user_id, await repo.now())
                state.focus = "New focus"
                state.revision = state.focus_version = state.epoch = 1
                new_card = card(user_id, topic="New direction")
                new_card.epoch = new_card.focus_version = 1
                new_turn = operation(
                    user_id, status="succeeded", output={"reply": "New turn"}
                )
                new_turn.epoch = 1
                writer.add_all([new_card, new_turn])
                await writer.commit()
        return row

    monkeypatch.setattr(LearningTutorRepository, "state", read_then_replace)
    async with client(user) as http:
        response = await asyncio.wait_for(http.get(URL + "/"), 5)
        assert response.status_code == 200
        body = response.json()
        assert (body["focus"], body["revision"], body["epoch"]) == (
            "Old focus" if existing else None,
            0,
            0,
        )
        assert [row["topic"] for row in body["recommendations"]] == (
            ["Electricity"] if existing else []
        )
        assert [row["output"]["reply"] for row in body["history"]["items"]] == (
            ["Old turn"] if existing else []
        )
        current = (await http.get(URL + "/")).json()
        assert (current["focus"], current["revision"], current["epoch"]) == (
            "New focus",
            1,
            1,
        )
        assert [row["topic"] for row in current["recommendations"]] == ["New direction"]
        assert [row["output"]["reply"] for row in current["history"]["items"]] == [
            "New turn"
        ]


@pytest.mark.anyio
async def test_mutation_response_does_not_reuse_precommit_identity_map():
    user = await account()

    class InterleavedService(LearningTutorService):
        async def read(self):
            # save_focus retains its state ORM object with expire_on_commit=False.
            # Another tab commits before the response read starts.
            async with db.async_session() as writer:
                state = await LearningTutorRepository(writer).lock_state(user.id)
                assert state.revision == 1
                state.focus = "Other tab"
                state.revision = state.focus_version = 2
                replacement = card(user.id)
                replacement.focus_version = 2
                writer.add(replacement)
                await writer.commit()
            return await super().read()

    async with db.async_session() as session:
        response = await InterleavedService(session, user).save_focus(
            "My edit", revision=0
        )
        assert (response.focus, response.revision, response.focus_version) == (
            "Other tab",
            2,
            2,
        )
        assert [row.topic for row in response.recommendations] == ["Electricity"]


@pytest.mark.anyio
async def test_state_api_does_not_hold_gate_connection_while_reading_snapshot():
    user = await account()
    engine = create_async_engine(
        db.engine.url, pool_size=1, max_overflow=0, pool_timeout=0.25
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def request_session():
        async with sessions() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[db.get_session] = request_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            assert (await http.get(URL + "/")).status_code == 200
            response = await http.put(
                URL + "/focus", json={"focus": "Physics", "revision": 0}
            )
            assert response.status_code == 200
            assert response.json()["focus"] == "Physics"
            assert (await http.get(URL + "/history")).status_code == 200
    finally:
        await engine.dispose()
