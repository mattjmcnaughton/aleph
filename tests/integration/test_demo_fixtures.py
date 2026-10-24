"""Demo data is repeatable and reset is scoped to the fictional account."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from aleph import db
from aleph.models import Path, User
from aleph.models.learning_tutor import LearningTutorOperation, LearningTutorState
from scripts.demo_fixtures import USER_ID, load_pack, seed, status


@pytest.mark.anyio
async def test_load_preserves_edits_and_reset_restores_only_demo_account():
    pack = load_pack()
    anchor = date(2026, 10, 24)
    other_id = uuid4()
    async with db.async_session.begin() as session:
        session.add(
            User(
                id=other_id,
                issuer="test",
                subject="unrelated",
                username="unrelated",
                display_name="Unrelated learner",
            )
        )
        assert await seed(session, pack, anchor) == "loaded"
    async with db.async_session.begin() as session:
        assert await status(session) == {
            "paths": 8,
            "beats": 4,
            "operations": 0,
            "recommendations": 0,
            "lessons": 32,
            "completed_lessons": 14,
            "briefs": 12,
        }
        ids = set(await session.scalars(select(Path.id).where(Path.user_id == USER_ID)))
        state = await session.get(LearningTutorState, USER_ID)
        assert state is not None
        state.focus = "My edited focus"
    async with db.async_session.begin() as session:
        assert await seed(session, pack, anchor) == "preserved"
        state = await session.get(LearningTutorState, USER_ID)
        assert state is not None
        assert state.focus == "My edited focus"
    async with db.async_session.begin() as session:
        assert await seed(session, pack, anchor, reset=True) == "reset"
    async with db.async_session() as session:
        state = await session.get(LearningTutorState, USER_ID)
        assert state is not None
        assert state.focus == pack.persona.focus
        assert state.epoch == 1
        assert (
            set(await session.scalars(select(Path.id).where(Path.user_id == USER_ID)))
            == ids
        )
        assert await session.get(User, other_id) is not None
        assert (await status(session))["completed_lessons"] == 14


@pytest.mark.anyio
async def test_reset_refuses_foreign_identity():
    async with db.async_session.begin() as session:
        session.add(
            User(
                id=USER_ID,
                issuer="test",
                subject="not-demo",
                username="not-demo",
                display_name="Not the demo learner",
            )
        )
    async with db.async_session.begin() as session:
        with pytest.raises(ValueError, match="another identity"):
            await seed(session, load_pack(), date(2026, 10, 24), reset=True)
        user = await session.get(User, USER_ID)
        assert user is not None
        assert user.subject == "not-demo"


@pytest.mark.anyio
async def test_running_operation_blocks_reset_and_failed_transaction_keeps_history():
    pack = load_pack()
    anchor = date(2026, 10, 24)
    operation_id = uuid4()
    now = datetime.now(UTC)
    async with db.async_session.begin() as session:
        await seed(session, pack, anchor)
        session.add(
            LearningTutorOperation(
                id=operation_id,
                user_id=USER_ID,
                epoch=0,
                request_id=uuid4(),
                request_hash="demo",
                kind="turn",
                revision=0,
                status="running",
                started_at=now,
                expires_at=now + timedelta(minutes=1),
            )
        )
    async with db.async_session.begin() as session:
        with pytest.raises(ValueError, match="operation is running"):
            await seed(session, pack, anchor, reset=True)
        operation = await session.get(LearningTutorOperation, operation_id)
        assert operation is not None
        operation.status = "succeeded"
    with pytest.raises(RuntimeError, match="abort rehearsal"):
        async with db.async_session.begin() as session:
            await seed(session, pack, anchor, reset=True)
            raise RuntimeError("abort rehearsal")
    async with db.async_session() as session:
        assert await session.get(LearningTutorOperation, operation_id) is not None
        state = await session.get(LearningTutorState, USER_ID)
        assert state is not None
        assert state.epoch == 0
        assert (await status(session))["paths"] == 8
