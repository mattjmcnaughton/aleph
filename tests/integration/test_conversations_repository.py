"""``ConversationRepository`` integration tests on a real Postgres database.

The load-bearing behaviour AL-200 must guarantee (Phase 2 TDD §4, D2, D3):

* **Lazy upsert** — the conversation row is created on the first completed turn
  and reused thereafter; "created" is reported so the service can emit
  ``tutor_conversation_started`` exactly once.
* **Turn-pair insert is the atomicity primitive (D2)** — a turn's two messages
  land at ``max+1`` / ``max+2`` together or not at all, and a position collision
  surfaces as a constraint violation rather than a silent reorder.
* **Thread load** — messages in position order, each with its lesson's title
  (the conversation DTO's shape, §6).
* **Delete** — "new conversation" drops the row; cascade removes the messages.
* **Ownership join** — message -> conversation -> path -> user, so the
  check-answer endpoint can 404 a message that is not the caller's.

Position assignment and cascade are SQL evaluated by the database, so this is an
integration test against real Postgres (fakes over mocks) rather than a unit
test.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select
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
    Unit,
    User,
)
from aleph.repositories import ConversationRepository

from .conftest import create_user, wait_until_lock_waiters

TUTOR_CHECK = {
    "stem": "Which binding owns the String?",
    "options": ["The first", "The second", "Neither"],
    "correct_index": 1,
    "explanation": "A move transfers ownership to the new binding.",
    "answered_index": None,
}


async def _make_path(session, *, user: User, topic: str = "Rust ownership") -> Path:
    path = Path(user_id=user.id, topic=topic, level=Level.SOME_EXPERIENCE)
    session.add(path)
    await session.flush()
    return path


async def _make_lesson(
    session, *, path: Path, position: int, title: str, unit: Unit | None = None
) -> Lesson:
    if unit is None:
        unit = Unit(path=path, position=1, title="Foundations", summary="s")
        session.add(unit)
        await session.flush()
    lesson = Lesson(
        unit=unit,
        path=path,
        position_in_path=position,
        position_in_unit=position,
        title=title,
        generation_state=LessonGenerationState.GENERATED,
        read_passage="Ownership is Rust's memory model.",
    )
    session.add(lesson)
    await session.flush()
    return lesson


async def _arrange_path_and_lesson() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Commit a user + path + lesson; return their ids."""
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user=user)
        lesson = await _make_lesson(
            session, path=path, position=1, title="What ownership is"
        )
        await session.commit()
        return user.id, path.id, lesson.id


async def _insert_turn(
    session,
    *,
    path_id: uuid.UUID,
    lesson_id: uuid.UUID,
    learner_content: str = "Why does a move invalidate the source?",
    source: MessageSource = MessageSource.TYPED,
    tutor_content: str = "Because ownership is unique.",
    tutor_check: dict | None = None,
) -> tuple[Message, Message]:
    repository = ConversationRepository(session)
    conversation, _created = await repository.upsert_for_path(path_id)
    return await repository.insert_turn(
        conversation_id=conversation.id,
        lesson_id=lesson_id,
        learner_content=learner_content,
        source=source,
        tutor_content=tutor_content,
        tutor_check=tutor_check,
    )


# --------------------------------------------------------------------------- #
# Lazy upsert
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_upsert_creates_once_then_reuses_the_same_conversation() -> None:
    _user_id, path_id, _lesson_id = await _arrange_path_and_lesson()

    async with db.async_session() as session:
        conversation, created = await ConversationRepository(session).upsert_for_path(
            path_id
        )
        await session.commit()
        first_id = conversation.id
    assert created is True

    async with db.async_session() as session:
        conversation, created = await ConversationRepository(session).upsert_for_path(
            path_id
        )
        await session.commit()
        assert conversation.id == first_id
    assert created is False

    async with db.async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Conversation)) == 1


@pytest.mark.anyio
async def test_get_for_path_returns_none_before_the_first_turn() -> None:
    _user_id, path_id, _lesson_id = await _arrange_path_and_lesson()

    async with db.async_session() as session:
        assert await ConversationRepository(session).get_for_path(path_id) is None


@pytest.mark.anyio
async def test_concurrent_upsert_yields_exactly_one_conversation() -> None:
    """Two sessions racing to create the thread: one row, both get it."""
    _user_id, path_id, _lesson_id = await _arrange_path_and_lesson()

    async def upsert() -> bool:
        async with db.async_session() as session:
            _conversation, created = await ConversationRepository(
                session
            ).upsert_for_path(path_id)
            await session.commit()
            return created

    results = await asyncio.gather(upsert(), upsert())

    async with db.async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Conversation)) == 1
    # Whoever lost the insert still received the winner's row, and only the
    # winner reports ``created`` (so ``tutor_conversation_started`` fires once).
    assert sorted(results) == [False, True]


# --------------------------------------------------------------------------- #
# Turn-pair insert (D2)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_insert_turn_assigns_dense_ordered_positions() -> None:
    _user_id, path_id, lesson_id = await _arrange_path_and_lesson()

    async with db.async_session() as session:
        learner, tutor = await _insert_turn(
            session, path_id=path_id, lesson_id=lesson_id
        )
        await session.commit()
        assert (learner.position, tutor.position) == (1, 2)

    async with db.async_session() as session:
        await _insert_turn(
            session,
            path_id=path_id,
            lesson_id=lesson_id,
            learner_content="And what about borrows?",
            source=MessageSource.SUGGESTION,
            tutor_content="A borrow does not move ownership.",
            tutor_check=TUTOR_CHECK,
        )
        await session.commit()

    async with db.async_session() as session:
        messages = list(
            (
                await session.execute(select(Message).order_by(Message.position))
            ).scalars()
        )
    assert [m.position for m in messages] == [1, 2, 3, 4]
    assert [m.role for m in messages] == [
        MessageRole.LEARNER,
        MessageRole.TUTOR,
        MessageRole.LEARNER,
        MessageRole.TUTOR,
    ]
    assert [m.source for m in messages] == [
        MessageSource.TYPED,
        None,
        MessageSource.SUGGESTION,
        None,
    ]
    assert [m.tutor_check for m in messages] == [None, None, None, TUTOR_CHECK]


@pytest.mark.anyio
async def test_turn_persists_whole_or_not_at_all() -> None:
    """D2: a turn that never settles leaves the learner message behind too."""
    _user_id, path_id, lesson_id = await _arrange_path_and_lesson()

    async with db.async_session() as session:
        await _insert_turn(session, path_id=path_id, lesson_id=lesson_id)
        # The stream failed after the rows were staged: the service's unit of
        # work rolls back and nothing at all is persisted.
        await session.rollback()

    async with db.async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Message)) == 0
        assert await session.scalar(select(func.count()).select_from(Conversation)) == 0


@pytest.mark.anyio
async def test_concurrent_turn_inserts_collide_loudly_never_reorder() -> None:
    """Bypassing the per-conversation lock (D9) must not silently interleave.

    Both writers read the same ``max(position)`` and try to claim ``1``/``2``;
    the UNIQUE constraint makes the loser fail rather than letting the two turns
    braid into a nonsensical thread.
    """
    _user_id, path_id, lesson_id = await _arrange_path_and_lesson()
    async with db.async_session() as session:
        conversation, _created = await ConversationRepository(session).upsert_for_path(
            path_id
        )
        await session.commit()
        conversation_id = conversation.id

    # Deterministic interleaving: the first writer stages its pair (so the rows
    # exist, uncommitted); the second then reads the same ``max`` of 0, tries to
    # claim position 1, and blocks on the unique index until the first commits.
    first_staged = asyncio.Event()
    first_may_commit = asyncio.Event()

    async def first() -> str:
        async with db.async_session() as session:
            await ConversationRepository(session).insert_turn(
                conversation_id=conversation_id,
                lesson_id=lesson_id,
                learner_content="question a",
                source=MessageSource.TYPED,
                tutor_content="reply a",
            )
            first_staged.set()
            await first_may_commit.wait()
            await session.commit()
            return "ok"

    async def second() -> str:
        await first_staged.wait()
        async with db.async_session() as session:
            insert = asyncio.create_task(
                ConversationRepository(session).insert_turn(
                    conversation_id=conversation_id,
                    lesson_id=lesson_id,
                    learner_content="question b",
                    source=MessageSource.TYPED,
                    tutor_content="reply b",
                )
            )
            await asyncio.wait_for(wait_until_lock_waiters(1), timeout=10)
            first_may_commit.set()
            try:
                await insert
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return "conflict"
            return "ok"

    results = await asyncio.gather(first(), second())
    assert results == ["ok", "conflict"], results

    async with db.async_session() as session:
        messages = list(
            (
                await session.execute(select(Message).order_by(Message.position))
            ).scalars()
        )
    # Exactly one turn landed, dense and ordered — and it is one writer's turn,
    # not a braid of both.
    assert [m.position for m in messages] == [1, 2]
    marker = messages[0].content.removeprefix("question ")
    assert messages[1].content == f"reply {marker}"


@pytest.mark.anyio
async def test_insert_turn_records_the_lesson_the_question_was_asked_in() -> None:
    _user_id, path_id, lesson_id = await _arrange_path_and_lesson()

    async with db.async_session() as session:
        path = await session.get(Path, path_id)
        assert path is not None
        unit = (await session.execute(select(Unit))).scalar_one()
        second = await _make_lesson(
            session, path=path, position=2, title="Borrowing", unit=unit
        )
        await session.commit()
        second_lesson_id = second.id

    async with db.async_session() as session:
        await _insert_turn(session, path_id=path_id, lesson_id=lesson_id)
        await session.commit()
    async with db.async_session() as session:
        await _insert_turn(session, path_id=path_id, lesson_id=second_lesson_id)
        await session.commit()

    async with db.async_session() as session:
        messages = list(
            (
                await session.execute(select(Message).order_by(Message.position))
            ).scalars()
        )
    assert [m.lesson_id for m in messages] == [
        lesson_id,
        lesson_id,
        second_lesson_id,
        second_lesson_id,
    ]


# --------------------------------------------------------------------------- #
# Thread load
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_load_thread_returns_position_order_with_lesson_titles() -> None:
    _user_id, path_id, lesson_id = await _arrange_path_and_lesson()

    async with db.async_session() as session:
        path = await session.get(Path, path_id)
        assert path is not None
        unit = (await session.execute(select(Unit))).scalar_one()
        second = await _make_lesson(
            session, path=path, position=2, title="Borrowing", unit=unit
        )
        await session.commit()
        second_lesson_id = second.id

    async with db.async_session() as session:
        await _insert_turn(session, path_id=path_id, lesson_id=lesson_id)
        await session.commit()
    async with db.async_session() as session:
        await _insert_turn(
            session,
            path_id=path_id,
            lesson_id=second_lesson_id,
            learner_content="And borrows?",
            tutor_content="A borrow does not move ownership.",
            tutor_check=TUTOR_CHECK,
        )
        await session.commit()

    async with db.async_session() as session:
        thread = await ConversationRepository(session).load_thread(path_id)

    assert [entry.message.position for entry in thread] == [1, 2, 3, 4]
    assert [entry.lesson_title for entry in thread] == [
        "What ownership is",
        "What ownership is",
        "Borrowing",
        "Borrowing",
    ]
    assert thread[3].message.tutor_check == TUTOR_CHECK
    assert thread[0].message.role is MessageRole.LEARNER


@pytest.mark.anyio
async def test_load_thread_is_empty_when_no_conversation_exists() -> None:
    _user_id, path_id, _lesson_id = await _arrange_path_and_lesson()

    async with db.async_session() as session:
        assert await ConversationRepository(session).load_thread(path_id) == []


@pytest.mark.anyio
async def test_load_thread_is_scoped_to_its_path() -> None:
    _user_id, path_id, lesson_id = await _arrange_path_and_lesson()
    async with db.async_session() as session:
        user = (await session.execute(select(User))).scalar_one()
        other_path = await _make_path(session, user=user, topic="US healthcare")
        other_lesson = await _make_lesson(
            session, path=other_path, position=1, title="Who pays"
        )
        await session.commit()
        other_path_id = other_path.id
        other_lesson_id = other_lesson.id

    async with db.async_session() as session:
        await _insert_turn(session, path_id=path_id, lesson_id=lesson_id)
        await session.commit()
    async with db.async_session() as session:
        await _insert_turn(
            session,
            path_id=other_path_id,
            lesson_id=other_lesson_id,
            learner_content="Who pays for what?",
            tutor_content="Payers vary.",
        )
        await session.commit()

    async with db.async_session() as session:
        repository = ConversationRepository(session)
        assert len(await repository.load_thread(path_id)) == 2
        assert [
            entry.message.content
            for entry in await repository.load_thread(other_path_id)
        ] == ["Who pays for what?", "Payers vary."]


# --------------------------------------------------------------------------- #
# Delete ("new conversation")
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_delete_for_path_drops_thread_and_is_idempotent() -> None:
    _user_id, path_id, lesson_id = await _arrange_path_and_lesson()
    async with db.async_session() as session:
        await _insert_turn(session, path_id=path_id, lesson_id=lesson_id)
        await session.commit()

    async with db.async_session() as session:
        assert await ConversationRepository(session).delete_for_path(path_id) is True
        await session.commit()

    async with db.async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Conversation)) == 0
        assert await session.scalar(select(func.count()).select_from(Message)) == 0
        # The path and its lessons are untouched: the tutor never writes Phase 1.
        assert await session.get(Path, path_id) is not None
        assert await session.scalar(select(func.count()).select_from(Lesson)) == 1

    async with db.async_session() as session:
        assert await ConversationRepository(session).delete_for_path(path_id) is False
        await session.commit()


@pytest.mark.anyio
async def test_next_turn_after_delete_starts_a_fresh_thread_at_position_one() -> None:
    _user_id, path_id, lesson_id = await _arrange_path_and_lesson()
    async with db.async_session() as session:
        await _insert_turn(session, path_id=path_id, lesson_id=lesson_id)
        await session.commit()
    async with db.async_session() as session:
        await ConversationRepository(session).delete_for_path(path_id)
        await session.commit()

    async with db.async_session() as session:
        repository = ConversationRepository(session)
        conversation, created = await repository.upsert_for_path(path_id)
        learner, tutor = await repository.insert_turn(
            conversation_id=conversation.id,
            lesson_id=lesson_id,
            learner_content="Starting over.",
            source=MessageSource.TYPED,
            tutor_content="Happy to.",
        )
        await session.commit()
    assert created is True
    assert (learner.position, tutor.position) == (1, 2)


# --------------------------------------------------------------------------- #
# Ownership join (check-answer endpoint, §6)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_get_message_for_user_enforces_the_ownership_join() -> None:
    user_id, path_id, lesson_id = await _arrange_path_and_lesson()
    async with db.async_session() as session:
        await _insert_turn(
            session, path_id=path_id, lesson_id=lesson_id, tutor_check=TUTOR_CHECK
        )
        await session.commit()

    async with db.async_session() as session:
        stranger = await create_user(session, username="stranger")
        await session.commit()
        stranger_id = stranger.id

    async with db.async_session() as session:
        tutor_message = (
            await session.execute(
                select(Message).where(Message.role == MessageRole.TUTOR)
            )
        ).scalar_one()
        message_id = tutor_message.id

    async with db.async_session() as session:
        repository = ConversationRepository(session)
        owned = await repository.get_message_for_user(
            message_id=message_id, user_id=user_id
        )
        assert owned is not None
        assert owned.id == message_id
        assert owned.tutor_check == TUTOR_CHECK

        # Someone else's message is indistinguishable from a missing one (the
        # 404-never-403 rule the endpoint relies on).
        assert (
            await repository.get_message_for_user(
                message_id=message_id, user_id=stranger_id
            )
            is None
        )
        assert (
            await repository.get_message_for_user(
                message_id=uuid.uuid4(), user_id=user_id
            )
            is None
        )
