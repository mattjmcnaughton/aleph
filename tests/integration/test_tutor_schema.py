"""Tutor schema integration tests against a real per-test Postgres database.

The invariants AL-200 must guarantee (Phase 2 TDD §4 / D3):

* **One conversation per path is a DB constraint** — ``UNIQUE (path_id)``, so a
  second insert fails loudly rather than forking the thread.
* **``position`` is the thread's total order** — ``UNIQUE (conversation_id,
  position)`` makes a collision loud, never a silent reorder.
* **Cascade** — deleting a conversation removes its messages; deleting a path
  removes both ("new conversation" and "delete path" need no new code).
* **``tutor_check`` round-trips** as JSONB, including ``NULL`` and a written
  ``answered_index`` (the check-answer endpoint's write, §6).
* **No CHECK constraints** — column applicability by role is app-enforced (§4),
  so the database must accept a learner row with ``source`` and a tutor row with
  ``tutor_check`` without opinion.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from aleph import db
from aleph.models import (
    Conversation,
    Lesson,
    LessonGenerationState,
    Level,
    Message,
    MessageRole,
    MessageSource,
    Path,
    PathStatus,
    Unit,
    User,
)

from .conftest import create_user


async def _build_path_with_lesson(session, *, user: User) -> tuple[Path, Lesson]:
    path = Path(
        user_id=user.id,
        topic="Rust ownership",
        level=Level.SOME_EXPERIENCE,
        status=PathStatus.READY,
    )
    unit = Unit(path=path, position=1, title="Foundations", summary="The basics.")
    lesson = Lesson(
        unit=unit,
        path=path,
        position_in_path=1,
        position_in_unit=1,
        title="What ownership is",
        generation_state=LessonGenerationState.GENERATED,
        read_passage="Ownership is Rust's memory model.",
    )
    session.add_all([path, unit, lesson])
    await session.flush()
    return path, lesson


def _turn(
    conversation: Conversation,
    lesson: Lesson,
    *,
    first_position: int = 1,
    tutor_check: dict | None = None,
) -> list[Message]:
    """A learner message + the tutor message it produced (CONTEXT.md: turn)."""
    return [
        Message(
            conversation=conversation,
            lesson_id=lesson.id,
            position=first_position,
            role=MessageRole.LEARNER,
            content="Why does a move invalidate the source?",
            source=MessageSource.TYPED,
        ),
        Message(
            conversation=conversation,
            lesson_id=lesson.id,
            position=first_position + 1,
            role=MessageRole.TUTOR,
            content="Because ownership is unique.",
            tutor_check=tutor_check,
        ),
    ]


@pytest.mark.anyio
async def test_conversation_and_messages_round_trip() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path, lesson = await _build_path_with_lesson(session, user=user)
        conversation = Conversation(path_id=path.id)
        session.add(conversation)
        session.add_all(_turn(conversation, lesson))
        await session.commit()
        path_id = path.id

    async with db.async_session() as session:
        saved = (await session.execute(select(Conversation))).scalar_one()
        assert saved.path_id == path_id

        messages = list(
            (
                await session.execute(select(Message).order_by(Message.position))
            ).scalars()
        )
        assert [m.position for m in messages] == [1, 2]
        assert [m.role for m in messages] == [MessageRole.LEARNER, MessageRole.TUTOR]
        # Column applicability is by role, app-enforced: the learner row carries
        # ``source`` and no check, the tutor row neither.
        assert messages[0].source is MessageSource.TYPED
        assert messages[0].tutor_check is None
        assert messages[1].source is None
        assert messages[1].tutor_check is None
        assert all(m.lesson_id == messages[0].lesson_id for m in messages)


@pytest.mark.anyio
async def test_second_conversation_for_same_path_rejected() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path, _lesson = await _build_path_with_lesson(session, user=user)
        session.add_all([Conversation(path_id=path.id), Conversation(path_id=path.id)])
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.anyio
async def test_duplicate_message_position_rejected() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path, lesson = await _build_path_with_lesson(session, user=user)
        conversation = Conversation(path_id=path.id)
        session.add(conversation)
        session.add_all(_turn(conversation, lesson))
        session.add(
            Message(
                conversation=conversation,
                lesson_id=lesson.id,
                position=2,
                role=MessageRole.TUTOR,
                content="A second reply at the same position.",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.anyio
async def test_same_position_in_a_different_conversation_is_allowed() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path_a, lesson_a = await _build_path_with_lesson(session, user=user)
        path_b = Path(user_id=user.id, topic="US healthcare", level=Level.NEW_TO_IT)
        unit_b = Unit(path=path_b, position=1, title="Payers", summary="s")
        lesson_b = Lesson(
            unit=unit_b,
            path=path_b,
            position_in_path=1,
            position_in_unit=1,
            title="Who pays",
        )
        conversation_a = Conversation(path_id=path_a.id)
        conversation_b = Conversation(path=path_b)
        session.add_all([path_b, unit_b, lesson_b, conversation_a, conversation_b])
        await session.flush()
        session.add_all(_turn(conversation_a, lesson_a))
        session.add_all(_turn(conversation_b, lesson_b))
        await session.commit()

    async with db.async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Message)) == 4


@pytest.mark.anyio
async def test_deleting_conversation_cascades_messages() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path, lesson = await _build_path_with_lesson(session, user=user)
        conversation = Conversation(path_id=path.id)
        session.add(conversation)
        session.add_all(_turn(conversation, lesson))
        await session.commit()
        conversation_id = conversation.id
        path_id = path.id

    # Core DELETE: the DB ON DELETE CASCADE does the work, not the ORM cascade.
    async with db.async_session() as session:
        await session.execute(
            delete(Conversation).where(Conversation.id == conversation_id)
        )
        await session.commit()

    async with db.async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Message)) == 0
        # "New conversation" drops the thread and nothing else: the path, its
        # lessons, and Phase 1 state are untouched (TDD §4).
        assert await session.get(Path, path_id) is not None
        assert await session.scalar(select(func.count()).select_from(Lesson)) == 1


@pytest.mark.anyio
async def test_deleting_path_cascades_conversation_and_messages() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path, lesson = await _build_path_with_lesson(session, user=user)
        conversation = Conversation(path_id=path.id)
        session.add(conversation)
        session.add_all(_turn(conversation, lesson))
        await session.commit()
        path_id = path.id
        user_id = user.id

    async with db.async_session() as session:
        await session.execute(delete(Path).where(Path.id == path_id))
        await session.commit()

    async with db.async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Conversation)) == 0
        assert await session.scalar(select(func.count()).select_from(Message)) == 0
        assert await session.get(User, user_id) is not None


@pytest.mark.anyio
async def test_deleting_lesson_cascades_its_messages() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path, lesson = await _build_path_with_lesson(session, user=user)
        conversation = Conversation(path_id=path.id)
        session.add(conversation)
        session.add_all(_turn(conversation, lesson))
        await session.commit()
        lesson_id = lesson.id

    async with db.async_session() as session:
        await session.execute(delete(Lesson).where(Lesson.id == lesson_id))
        await session.commit()

    async with db.async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Message)) == 0
        # The conversation itself survives losing one lesson's messages.
        assert await session.scalar(select(func.count()).select_from(Conversation)) == 1


@pytest.mark.anyio
async def test_tutor_check_round_trips_including_null_and_answered_index() -> None:
    check = {
        "stem": "Which value owns the String?",
        "options": ["The binding", "The heap", "The compiler"],
        "correct_index": 0,
        "explanation": "Each value has exactly one owning binding.",
        "answered_index": None,
    }
    async with db.async_session() as session:
        user = await create_user(session)
        path, lesson = await _build_path_with_lesson(session, user=user)
        conversation = Conversation(path_id=path.id)
        session.add(conversation)
        session.add_all(_turn(conversation, lesson, tutor_check=check))
        await session.commit()

    async with db.async_session() as session:
        tutor_message = (
            await session.execute(
                select(Message).where(Message.role == MessageRole.TUTOR)
            )
        ).scalar_one()
        assert tutor_message.tutor_check == check
        assert tutor_message.tutor_check is not None
        assert tutor_message.tutor_check["answered_index"] is None
        message_id = tutor_message.id

    # The check-answer endpoint (§6) writes ``answered_index`` into the payload.
    async with db.async_session() as session:
        tutor_message = await session.get(Message, message_id)
        assert tutor_message is not None
        assert tutor_message.tutor_check is not None
        tutor_message.tutor_check = {**tutor_message.tutor_check, "answered_index": 2}
        await session.commit()

    async with db.async_session() as session:
        tutor_message = await session.get(Message, message_id)
        assert tutor_message is not None
        assert tutor_message.tutor_check is not None
        assert tutor_message.tutor_check["answered_index"] == 2
        assert tutor_message.tutor_check["correct_index"] == 0
        assert tutor_message.tutor_check["options"] == check["options"]


@pytest.mark.anyio
async def test_message_enums_store_their_string_values() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path, lesson = await _build_path_with_lesson(session, user=user)
        conversation = Conversation(path_id=path.id)
        session.add(conversation)
        learner, tutor = _turn(conversation, lesson)
        learner.source = MessageSource.SUGGESTION
        session.add_all([learner, tutor])
        await session.commit()

    # The enums store their values (not the Python member names): values_callable.
    async with db.async_session() as session:
        rows = (
            await session.execute(
                text("SELECT role, source FROM messages ORDER BY position")
            )
        ).all()
    assert rows == [("learner", "suggestion"), ("tutor", None)]


@pytest.mark.anyio
async def test_role_columns_carry_no_check_constraints() -> None:
    """Applicability is app-enforced (§4): the DB accepts either shape."""
    async with db.async_session() as session:
        user = await create_user(session)
        path, lesson = await _build_path_with_lesson(session, user=user)
        conversation = Conversation(path_id=path.id)
        session.add(conversation)
        session.add(
            Message(
                conversation=conversation,
                lesson_id=lesson.id,
                position=1,
                role=MessageRole.LEARNER,
                content="A learner row the DB does not police.",
                source=None,
                tutor_check={"stem": "s", "options": ["a", "b", "c"]},
            )
        )
        await session.commit()

    async with db.async_session() as session:
        constraints = (
            await session.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'messages'::regclass AND contype = 'c'"
                )
            )
        ).scalars()
        assert list(constraints) == []
