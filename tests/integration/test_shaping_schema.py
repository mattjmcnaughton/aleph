"""Shaping schema integration tests against a real per-test Postgres database.

The invariants AL-300 must guarantee (Phase 2B TDD §4 / D3):

* **Two threads per path, never two of either** — ``UNIQUE (path_id, kind)``
  replaces ``UNIQUE (path_id)``, so a path may hold its in-lesson thread *and*
  its **Shaping conversation** while a duplicate of either still fails loudly.
* **``kind`` defaults to ``lesson``** — the 2A row shape keeps working unchanged.
* **A cleared thread leaves the Change history standing** —
  ``path_changes.message_id`` is ``ON DELETE SET NULL``, deliberately *not*
  cascade: deleting messages nulls the reference and keeps every row (PRD §5.8).
* **Deleting a path takes everything** — both threads, their messages, and the
  changes.
* **``proposal`` / ``payload`` round-trip as JSONB**, ``NULL`` included, and
  **``revision_instruction``** round-trips as text.
* **No CHECK constraints** — applicability by role and by thread kind is
  app-enforced (§4), as ``source``/``tutor_check`` already are.

Cascade and defaults are decided by the database, so these are integration
tests against real Postgres (fakes over mocks) rather than unit tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from aleph import db
from aleph.models import (
    Conversation,
    ConversationKind,
    Lesson,
    LessonGenerationState,
    Level,
    Message,
    MessageRole,
    MessageSource,
    Path,
    PathChange,
    PathChangeKind,
    PathChangeStatus,
    PathStatus,
    Unit,
    User,
)

from .conftest import create_user

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

PROPOSAL: dict[str, Any] = {
    "summary": "Adds 2 lessons on lifetimes.",
    "operations": [
        {
            "kind": "add_lessons",
            "insert_at_position": 2,
            "new_unit": {"title": "Lifetimes", "summary": "Naming borrows."},
            "lessons": [{"title": "Lifetime basics"}, {"title": "Elision rules"}],
            "rationale": "The path never covers lifetimes explicitly.",
            "estimated_minutes": 10,
        }
    ],
}
CHANGE_PAYLOAD: dict[str, Any] = {
    **PROPOSAL,
    "created_lesson_ids": ["8f14e45f-ceea-467a-9e35-1e1c39f0f0f0"],
}


async def _build_path_with_lesson(
    session: AsyncSession, *, user: User
) -> tuple[Path, Lesson]:
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
    learner_content: str = "Add something on lifetimes.",
    tutor_content: str = "Here is what I would add.",
    proposal: dict[str, Any] | None = None,
) -> list[Message]:
    return [
        Message(
            conversation=conversation,
            lesson_id=lesson.id,
            position=1,
            role=MessageRole.LEARNER,
            content=learner_content,
            source=MessageSource.TYPED,
        ),
        Message(
            conversation=conversation,
            lesson_id=lesson.id,
            position=2,
            role=MessageRole.TUTOR,
            content=tutor_content,
            proposal=proposal,
        ),
    ]


async def _arrange_shaped_path() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A path with both threads and one Change on its shaping proposal.

    Returns ``(path_id, shaping_conversation_id, change_id)``.
    """
    async with db.async_session() as session:
        user = await create_user(session)
        path, lesson = await _build_path_with_lesson(session, user=user)
        in_lesson = Conversation(path_id=path.id)
        shaping = Conversation(path_id=path.id, kind=ConversationKind.SHAPING)
        session.add_all([in_lesson, shaping])
        await session.flush()
        session.add_all(
            _turn(
                in_lesson,
                lesson,
                learner_content="Why does a move invalidate the source?",
                tutor_content="Because ownership is unique.",
            )
        )
        proposal_messages = _turn(shaping, lesson, proposal=PROPOSAL)
        session.add_all(proposal_messages)
        await session.flush()
        change = PathChange(
            path_id=path.id,
            message_id=proposal_messages[1].id,
            kind=PathChangeKind.ADD_LESSONS,
            payload=CHANGE_PAYLOAD,
        )
        session.add(change)
        await session.commit()
        return path.id, shaping.id, change.id


# --------------------------------------------------------------------------- #
# Conversation kind + the widened uniqueness (D3)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_conversation_defaults_to_the_lesson_kind() -> None:
    """A row written the 2A way is a ``lesson`` thread — the backfill's shape."""
    async with db.async_session() as session:
        user = await create_user(session)
        path, _lesson = await _build_path_with_lesson(session, user=user)
        session.add(Conversation(path_id=path.id))
        await session.commit()

    async with db.async_session() as session:
        conversation = (await session.execute(select(Conversation))).scalar_one()
        assert conversation.kind is ConversationKind.LESSON


@pytest.mark.anyio
async def test_a_path_may_hold_one_thread_of_each_kind() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path, _lesson = await _build_path_with_lesson(session, user=user)
        session.add_all(
            [
                Conversation(path_id=path.id, kind=ConversationKind.LESSON),
                Conversation(path_id=path.id, kind=ConversationKind.SHAPING),
            ]
        )
        await session.commit()

    async with db.async_session() as session:
        kinds = list(
            (
                await session.execute(
                    select(Conversation.kind).order_by(Conversation.kind)
                )
            ).scalars()
        )
    assert kinds == [ConversationKind.LESSON, ConversationKind.SHAPING]


@pytest.mark.anyio
@pytest.mark.parametrize("kind", [ConversationKind.LESSON, ConversationKind.SHAPING])
async def test_a_duplicate_thread_of_one_kind_is_still_rejected(
    kind: ConversationKind,
) -> None:
    """Widening the constraint did not weaken it: a thread cannot fork."""
    async with db.async_session() as session:
        user = await create_user(session)
        path, _lesson = await _build_path_with_lesson(session, user=user)
        session.add_all(
            [
                Conversation(path_id=path.id, kind=kind),
                Conversation(path_id=path.id, kind=kind),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.anyio
async def test_the_same_kind_on_a_different_path_is_allowed() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path_a, _lesson_a = await _build_path_with_lesson(session, user=user)
        path_b = Path(user_id=user.id, topic="US healthcare", level=Level.NEW_TO_IT)
        session.add(path_b)
        await session.flush()
        session.add_all(
            [
                Conversation(path_id=path_a.id, kind=ConversationKind.SHAPING),
                Conversation(path_id=path_b.id, kind=ConversationKind.SHAPING),
            ]
        )
        await session.commit()

    async with db.async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Conversation)) == 2


@pytest.mark.anyio
async def test_shaping_enums_store_their_string_values() -> None:
    """The enums store their CONTEXT.md values, not the Python member names."""
    path_id, _conversation_id, _change_id = await _arrange_shaped_path()
    assert path_id is not None

    async with db.async_session() as session:
        conversation_kinds = (
            await session.execute(text("SELECT kind FROM conversations ORDER BY kind"))
        ).all()
        change_rows = (
            await session.execute(text("SELECT kind, status FROM path_changes"))
        ).all()
    assert conversation_kinds == [("lesson",), ("shaping",)]
    assert change_rows == [("add_lessons", "applied")]


# --------------------------------------------------------------------------- #
# Payload columns
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_proposal_round_trips_as_jsonb_including_null() -> None:
    _path_id, conversation_id, _change_id = await _arrange_shaped_path()

    async with db.async_session() as session:
        proposals = list(
            (
                await session.execute(
                    select(Message.proposal)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.position)
                )
            ).scalars()
        )
    # Learner row carries none; the tutor row carries the whole payload,
    # nested objects and lists intact.
    assert proposals == [None, PROPOSAL]


@pytest.mark.anyio
async def test_change_payload_round_trips_as_jsonb() -> None:
    _path_id, _conversation_id, change_id = await _arrange_shaped_path()

    async with db.async_session() as session:
        change = await session.get(PathChange, change_id)
        assert change is not None
        assert change.payload == CHANGE_PAYLOAD
        assert change.status is PathChangeStatus.APPLIED
        assert change.undone_at is None


@pytest.mark.anyio
async def test_revision_instruction_round_trips_and_defaults_to_null() -> None:
    """Set by apply, cleared on ``generated`` (D7); ``NULL`` on every other lesson."""
    async with db.async_session() as session:
        user = await create_user(session)
        _path, lesson = await _build_path_with_lesson(session, user=user)
        await session.commit()
        lesson_id = lesson.id
        assert lesson.revision_instruction is None

    async with db.async_session() as session:
        lesson = await session.get(Lesson, lesson_id)
        assert lesson is not None
        lesson.revision_instruction = "More worked examples, less theory."
        lesson.generation_state = LessonGenerationState.UNGENERATED
        await session.commit()

    async with db.async_session() as session:
        stored = await session.scalar(
            select(Lesson.revision_instruction).where(Lesson.id == lesson_id)
        )
    assert stored == "More worked examples, less theory."


@pytest.mark.anyio
async def test_shaping_columns_carry_no_check_constraints() -> None:
    """Applicability is app-enforced (§4): the DB accepts either shape."""
    async with db.async_session() as session:
        user = await create_user(session)
        path, lesson = await _build_path_with_lesson(session, user=user)
        # A proposal on a *lesson*-thread learner row: nonsense the application
        # forbids and the database has no opinion about.
        conversation = Conversation(path_id=path.id)
        session.add(conversation)
        await session.flush()
        session.add(
            Message(
                conversation=conversation,
                lesson_id=lesson.id,
                position=1,
                role=MessageRole.LEARNER,
                content="A learner row the DB does not police.",
                proposal=PROPOSAL,
            )
        )
        await session.commit()

    async with db.async_session() as session:
        constraints = (
            await session.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid IN ("
                    "  'messages'::regclass, 'path_changes'::regclass"
                    ") AND contype = 'c'"
                )
            )
        ).scalars()
        assert list(constraints) == []


# --------------------------------------------------------------------------- #
# The history outlives the thread (D3) — the load-bearing cascade choice
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_clearing_a_thread_leaves_the_change_with_a_null_message() -> None:
    """ "New conversation" deletes messages; the Change history survives (PRD §5.8).

    This is why ``message_id`` is ``SET NULL`` and not ``CASCADE``: history
    belongs to the path, and a learner who starts a fresh conversation has not
    asked to forget what they already applied.
    """
    path_id, conversation_id, change_id = await _arrange_shaped_path()

    async with db.async_session() as session:
        await session.execute(
            delete(Conversation).where(Conversation.id == conversation_id)
        )
        await session.commit()

    async with db.async_session() as session:
        change = await session.get(PathChange, change_id)
        assert change is not None
        assert change.message_id is None
        # Everything else about the change is intact — the payload is what undo
        # needs, and it never lived on the message.
        assert change.path_id == path_id
        assert change.payload == CHANGE_PAYLOAD
        assert change.status is PathChangeStatus.APPLIED
        # Only the shaping thread went: the in-lesson thread is untouched (W21).
        remaining = (await session.execute(select(Conversation))).scalars().all()
        assert [c.kind for c in remaining] == [ConversationKind.LESSON]


@pytest.mark.anyio
async def test_deleting_only_the_proposal_message_nulls_the_reference() -> None:
    """The ``SET NULL`` is on the message FK itself, not just on the cascade."""
    _path_id, conversation_id, change_id = await _arrange_shaped_path()

    async with db.async_session() as session:
        await session.execute(
            delete(Message).where(Message.conversation_id == conversation_id)
        )
        await session.commit()

    async with db.async_session() as session:
        change = await session.get(PathChange, change_id)
        assert change is not None
        assert change.message_id is None
        # The thread row itself is still there — only its messages went.
        assert await session.get(Conversation, conversation_id) is not None


@pytest.mark.anyio
async def test_deleting_the_path_cascades_both_threads_and_the_changes() -> None:
    """Delete path still needs no application code (Phase 2 §4, extended)."""
    path_id, _conversation_id, _change_id = await _arrange_shaped_path()

    async with db.async_session() as session:
        user_id = (await session.execute(select(User.id))).scalar_one()
        await session.execute(delete(Path).where(Path.id == path_id))
        await session.commit()

    async with db.async_session() as session:
        for model in (Conversation, Message, PathChange, Lesson, Unit):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
        # The account is not collateral damage.
        assert await session.get(User, user_id) is not None
