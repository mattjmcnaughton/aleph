"""Shaping data-access integration tests (Phase 2B TDD §4, D2, D3).

Three seams, one real Postgres:

* **``ChangeRepository``** — a **Change** is created, read back, and listed
  newest-first. History is the path's, so listing is scoped to the path and
  ordered by when structure landed.
* **``ConversationRepository`` takes a kind** — a path carries an in-lesson
  thread *and* a **Shaping conversation**, and every query has to say which.
  There is no default: naming the kind is required, so a query can never mean
  the other rail's thread by omission (D3).
* **The engagement facts (D2)** — the repository supplies ``completed_at`` and
  "an Attempt exists", the pure predicate in
  :mod:`aleph.domains.engagement` decides. Ordering and the ``EXISTS`` are SQL,
  so they are proven here rather than against a fake.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from aleph import db
from aleph.domains.engagement import (
    LessonEngagement,
    first_shapeable_position,
    is_engaged,
)
from aleph.models import (
    Attempt,
    Conversation,
    ConversationKind,
    Lesson,
    LessonGenerationState,
    Level,
    Message,
    MessageSource,
    Path,
    PathChange,
    PathChangeKind,
    PathChangeStatus,
    QuickCheck,
    Unit,
    User,
)
from aleph.repositories import (
    ChangeRepository,
    ConversationRepository,
    LessonRepository,
)

from .conftest import create_user

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

ADD_PAYLOAD: dict[str, Any] = {
    "summary": "Adds 2 lessons on lifetimes.",
    "operations": [
        {
            "kind": "add_lessons",
            "insert_at_position": 3,
            "new_unit": None,
            "lessons": [{"title": "Lifetime basics"}, {"title": "Elision rules"}],
            "rationale": "The path never covers lifetimes explicitly.",
            "estimated_minutes": 10,
        }
    ],
    "created_lesson_ids": [],
}
REVISE_PAYLOAD: dict[str, Any] = {
    "summary": "Rewrites 'Borrowing' with more worked examples.",
    "operations": [
        {
            "kind": "revise_lesson",
            "instruction": "More worked examples, less theory.",
            "new_title": None,
            "rationale": "The learner asked for concrete code.",
        }
    ],
    "snapshot": {
        "read_passage": "A borrow does not move ownership.",
        "title": "Borrowing",
    },
}
PROPOSAL: dict[str, Any] = {
    "summary": ADD_PAYLOAD["summary"],
    "operations": ADD_PAYLOAD["operations"],
}


# --------------------------------------------------------------------------- #
# Arrange helpers
# --------------------------------------------------------------------------- #


async def _make_path(session: AsyncSession, *, user: User, topic: str) -> Path:
    path = Path(user_id=user.id, topic=topic, level=Level.SOME_EXPERIENCE)
    session.add(path)
    await session.flush()
    return path


async def _make_lesson(
    session: AsyncSession,
    *,
    path: Path,
    unit: Unit,
    position: int,
    title: str,
) -> Lesson:
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


async def _arrange_path(topic: str = "Rust ownership") -> tuple[uuid.UUID, uuid.UUID]:
    """Commit a user + path + one lesson; return ``(path_id, lesson_id)``."""
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _make_path(session, user=user, topic=topic)
        unit = Unit(path=path, position=1, title="Foundations", summary="s")
        session.add(unit)
        await session.flush()
        lesson = await _make_lesson(
            session, path=path, unit=unit, position=1, title="What ownership is"
        )
        await session.commit()
        return path.id, lesson.id


async def _create_change(
    *,
    path_id: uuid.UUID,
    message_id: uuid.UUID | None = None,
    kind: PathChangeKind = PathChangeKind.ADD_LESSONS,
    payload: dict[str, Any] | None = None,
) -> uuid.UUID:
    """Create one Change in its own transaction (so ``applied_at`` advances)."""
    async with db.async_session() as session:
        change = await ChangeRepository(session).create(
            path_id=path_id,
            message_id=message_id,
            kind=kind,
            payload=payload if payload is not None else ADD_PAYLOAD,
        )
        await session.commit()
        return change.id


# --------------------------------------------------------------------------- #
# ChangeRepository (§4 / D3)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_create_records_an_applied_change_with_its_payload() -> None:
    """A Change exists because Apply committed: it is born ``applied``."""
    path_id, _lesson_id = await _arrange_path()

    change_id = await _create_change(path_id=path_id)

    async with db.async_session() as session:
        change = await ChangeRepository(session).get(change_id)
        assert change is not None
        assert change.path_id == path_id
        assert change.kind is PathChangeKind.ADD_LESSONS
        assert change.payload == ADD_PAYLOAD
        # Status and the apply stamp are defaulted, not passed in: a caller
        # cannot create an already-undone change.
        assert change.status is PathChangeStatus.APPLIED
        assert change.applied_at is not None
        assert change.undone_at is None


@pytest.mark.anyio
async def test_get_returns_none_for_an_unknown_change() -> None:
    await _arrange_path()

    async with db.async_session() as session:
        assert await ChangeRepository(session).get(uuid.uuid4()) is None


@pytest.mark.anyio
async def test_list_for_path_returns_changes_newest_first() -> None:
    """The Change history reads newest-first (§6), not insertion order."""
    path_id, _lesson_id = await _arrange_path()

    first = await _create_change(path_id=path_id)
    second = await _create_change(
        path_id=path_id, kind=PathChangeKind.REVISE_LESSON, payload=REVISE_PAYLOAD
    )
    third = await _create_change(path_id=path_id)

    async with db.async_session() as session:
        history = await ChangeRepository(session).list_for_path(path_id)

    assert [change.id for change in history] == [third, second, first]
    assert [change.kind for change in history] == [
        PathChangeKind.ADD_LESSONS,
        PathChangeKind.REVISE_LESSON,
        PathChangeKind.ADD_LESSONS,
    ]


@pytest.mark.anyio
async def test_list_for_path_is_scoped_to_its_path() -> None:
    """History is owned by the path — another path's changes never leak in."""
    path_id, _lesson_id = await _arrange_path()
    async with db.async_session() as session:
        user = (await session.execute(select(User))).scalar_one()
        other = await _make_path(session, user=user, topic="US healthcare")
        await session.commit()
        other_path_id = other.id

    mine = await _create_change(path_id=path_id)
    await _create_change(path_id=other_path_id)

    async with db.async_session() as session:
        repository = ChangeRepository(session)
        assert [change.id for change in await repository.list_for_path(path_id)] == [
            mine
        ]
        assert len(await repository.list_for_path(other_path_id)) == 1


@pytest.mark.anyio
async def test_list_for_path_is_empty_for_a_path_with_no_changes() -> None:
    path_id, _lesson_id = await _arrange_path()

    async with db.async_session() as session:
        assert await ChangeRepository(session).list_for_path(path_id) == []


@pytest.mark.anyio
async def test_list_for_path_includes_undone_changes() -> None:
    """Undo is a status, not a delete: an undone Change stays in the history."""
    path_id, _lesson_id = await _arrange_path()
    change_id = await _create_change(path_id=path_id)

    async with db.async_session() as session:
        change = await session.get(PathChange, change_id)
        assert change is not None
        change.status = PathChangeStatus.UNDONE
        change.undone_at = func.now()
        await session.commit()

    async with db.async_session() as session:
        history = await ChangeRepository(session).list_for_path(path_id)
    assert [change.status for change in history] == [PathChangeStatus.UNDONE]
    assert history[0].undone_at is not None


# --------------------------------------------------------------------------- #
# ConversationRepository, kind-scoped (D3)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_path_carries_one_conversation_of_each_kind() -> None:
    path_id, _lesson_id = await _arrange_path()

    async with db.async_session() as session:
        repository = ConversationRepository(session)
        lesson_thread, lesson_created = await repository.upsert_for_path(
            path_id, kind=ConversationKind.LESSON
        )
        shaping_thread, shaping_created = await repository.upsert_for_path(
            path_id, kind=ConversationKind.SHAPING
        )
        await session.commit()
        lesson_id, shaping_id = lesson_thread.id, shaping_thread.id

    assert (lesson_created, shaping_created) == (True, True)
    assert lesson_id != shaping_id

    async with db.async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Conversation)) == 2


@pytest.mark.anyio
async def test_upsert_persists_the_kind_it_was_asked_for() -> None:
    """The row is stamped with the caller's kind — 2A's callers name ``lesson``."""
    path_id, _lesson_id = await _arrange_path()

    async with db.async_session() as session:
        conversation, _created = await ConversationRepository(session).upsert_for_path(
            path_id, kind=ConversationKind.LESSON
        )
        await session.commit()
        assert conversation.kind is ConversationKind.LESSON


@pytest.mark.anyio
async def test_upsert_of_the_same_kind_reuses_the_row() -> None:
    path_id, _lesson_id = await _arrange_path()

    async with db.async_session() as session:
        first, created = await ConversationRepository(session).upsert_for_path(
            path_id, kind=ConversationKind.SHAPING
        )
        await session.commit()
        first_id = created and first.id

    async with db.async_session() as session:
        again, created_again = await ConversationRepository(session).upsert_for_path(
            path_id, kind=ConversationKind.SHAPING
        )
        await session.commit()
        assert again.id == first_id
    assert created_again is False


@pytest.mark.anyio
async def test_get_for_path_is_kind_scoped() -> None:
    path_id, _lesson_id = await _arrange_path()
    async with db.async_session() as session:
        await ConversationRepository(session).upsert_for_path(
            path_id, kind=ConversationKind.SHAPING
        )
        await session.commit()

    async with db.async_session() as session:
        repository = ConversationRepository(session)
        # The shaping thread exists; the in-lesson one does not — asking for the
        # wrong kind must not hand back the other thread.
        assert (
            await repository.get_for_path(path_id, kind=ConversationKind.LESSON) is None
        )
        shaping = await repository.get_for_path(path_id, kind=ConversationKind.SHAPING)
        assert shaping is not None
        assert shaping.kind is ConversationKind.SHAPING


@pytest.mark.anyio
async def test_load_thread_is_kind_scoped() -> None:
    """The in-lesson rail never shows shaping turns, and vice versa (PRD §5.8)."""
    path_id, lesson_id = await _arrange_path()

    async with db.async_session() as session:
        repository = ConversationRepository(session)
        lesson_thread, _ = await repository.upsert_for_path(
            path_id, kind=ConversationKind.LESSON
        )
        await repository.insert_turn(
            conversation_id=lesson_thread.id,
            lesson_id=lesson_id,
            learner_content="Why does a move invalidate the source?",
            source=MessageSource.TYPED,
            tutor_content="Because ownership is unique.",
        )
        shaping_thread, _ = await repository.upsert_for_path(
            path_id, kind=ConversationKind.SHAPING
        )
        await repository.insert_turn(
            conversation_id=shaping_thread.id,
            lesson_id=lesson_id,
            learner_content="Add something on lifetimes.",
            source=MessageSource.TYPED,
            tutor_content="Here is what I would add.",
            proposal=PROPOSAL,
        )
        await session.commit()

    async with db.async_session() as session:
        repository = ConversationRepository(session)
        in_lesson = await repository.load_thread(path_id, kind=ConversationKind.LESSON)
        shaping = await repository.load_thread(path_id, kind=ConversationKind.SHAPING)

    assert [entry.message.content for entry in in_lesson] == [
        "Why does a move invalidate the source?",
        "Because ownership is unique.",
    ]
    assert [entry.message.content for entry in shaping] == [
        "Add something on lifetimes.",
        "Here is what I would add.",
    ]
    # The Proposal rides on the tutor row, exactly as a Tutor check does.
    assert [entry.message.proposal for entry in shaping] == [None, PROPOSAL]
    assert [entry.message.proposal for entry in in_lesson] == [None, None]


@pytest.mark.anyio
async def test_delete_for_path_drops_only_the_named_kind() -> None:
    """ "New conversation" on one rail must not clear the other."""
    path_id, lesson_id = await _arrange_path()
    async with db.async_session() as session:
        repository = ConversationRepository(session)
        for kind in (ConversationKind.LESSON, ConversationKind.SHAPING):
            conversation, _ = await repository.upsert_for_path(path_id, kind=kind)
            await repository.insert_turn(
                conversation_id=conversation.id,
                lesson_id=lesson_id,
                learner_content=f"a {kind.value} question",
                source=MessageSource.TYPED,
                tutor_content=f"a {kind.value} reply",
            )
        await session.commit()

    async with db.async_session() as session:
        repository = ConversationRepository(session)
        assert (
            await repository.delete_for_path(path_id, kind=ConversationKind.SHAPING)
            is True
        )
        await session.commit()

    async with db.async_session() as session:
        repository = ConversationRepository(session)
        assert (
            await repository.get_for_path(path_id, kind=ConversationKind.LESSON)
            is not None
        )
        assert (
            await repository.get_for_path(path_id, kind=ConversationKind.SHAPING)
            is None
        )
        # Only the shaping thread's messages went with it.
        assert (
            len(await repository.load_thread(path_id, kind=ConversationKind.LESSON))
            == 2
        )
        # Idempotent: a second clear is a no-op, not an error (§6, 204 either way).
        assert (
            await repository.delete_for_path(path_id, kind=ConversationKind.SHAPING)
            is False
        )


@pytest.mark.anyio
async def test_a_second_thread_of_the_same_kind_is_rejected() -> None:
    """The widened unique constraint still forbids forking a thread."""
    path_id, _lesson_id = await _arrange_path()

    async with db.async_session() as session:
        session.add_all(
            [
                Conversation(path_id=path_id, kind=ConversationKind.SHAPING),
                Conversation(path_id=path_id, kind=ConversationKind.SHAPING),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()


# --------------------------------------------------------------------------- #
# Engagement facts (D2)
# --------------------------------------------------------------------------- #


async def _arrange_three_lessons(
    username: str = "test-user",
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """A path of three generated lessons, each with a Quick check."""
    async with db.async_session() as session:
        user = await create_user(session, username=username)
        path = await _make_path(session, user=user, topic="Rust ownership")
        unit = Unit(path=path, position=1, title="Foundations", summary="s")
        session.add(unit)
        await session.flush()
        lesson_ids = []
        for position, title in enumerate(("Ownership", "Borrowing", "Lifetimes"), 1):
            lesson = await _make_lesson(
                session, path=path, unit=unit, position=position, title=title
            )
            session.add(
                QuickCheck(
                    lesson=lesson,
                    stem=f"What about {title}?",
                    options=["a", "b", "c"],
                    correct_index=0,
                    explanation="Because.",
                )
            )
            lesson_ids.append(lesson.id)
        await session.commit()
        return path.id, lesson_ids


@pytest.mark.anyio
async def test_engagement_facts_report_no_attempt_on_an_untouched_path() -> None:
    path_id, lesson_ids = await _arrange_three_lessons()

    async with db.async_session() as session:
        rows = await LessonRepository(session).list_for_path_with_engagement(path_id)

    assert [lesson.id for lesson, _ in rows] == lesson_ids
    assert [has_attempt for _, has_attempt in rows] == [False, False, False]


@pytest.mark.anyio
async def test_engagement_facts_see_an_attempt_on_the_lessons_quick_check() -> None:
    """The repository supplies the fact; :func:`is_engaged` draws the line."""
    path_id, lesson_ids = await _arrange_three_lessons()

    async with db.async_session() as session:
        user = (await session.execute(select(User))).scalar_one()
        check = (
            await session.execute(
                select(QuickCheck).where(QuickCheck.lesson_id == lesson_ids[0])
            )
        ).scalar_one()
        session.add(
            Attempt(
                quick_check_id=check.id,
                user_id=user.id,
                selected_index=0,
                is_correct=True,
            )
        )
        # The second lesson is complete but never attempted — the other half of
        # the D2 disjunction (the Quick check is non-gating).
        second = await session.get(Lesson, lesson_ids[1])
        assert second is not None
        second.completed_at = func.now()
        await session.commit()

    async with db.async_session() as session:
        rows = await LessonRepository(session).list_for_path_with_engagement(path_id)

    engagement = [
        LessonEngagement(
            position_in_path=lesson.position_in_path,
            completed_at=lesson.completed_at,
            has_attempt=has_attempt,
        )
        for lesson, has_attempt in rows
    ]
    assert [is_engaged(lesson) for lesson in engagement] == [True, True, False]
    assert first_shapeable_position(engagement) == 3


@pytest.mark.anyio
async def test_engagement_facts_are_ordered_by_position_and_scoped_to_the_path() -> (
    None
):
    path_id, lesson_ids = await _arrange_three_lessons()
    other_path_id, _other_lessons = await _arrange_three_lessons(username="other-user")

    async with db.async_session() as session:
        rows = await LessonRepository(session).list_for_path_with_engagement(path_id)

    assert [lesson.position_in_path for lesson, _ in rows] == [1, 2, 3]
    assert {lesson.id for lesson, _ in rows} == set(lesson_ids)
    assert other_path_id != path_id


@pytest.mark.anyio
async def test_engagement_facts_tolerate_a_lesson_without_a_quick_check() -> None:
    """An ungenerated lesson has no check yet — and is simply unengaged."""
    path_id, _lesson_id = await _arrange_path()

    async with db.async_session() as session:
        rows = await LessonRepository(session).list_for_path_with_engagement(path_id)

    assert [has_attempt for _, has_attempt in rows] == [False]


@pytest.mark.anyio
async def test_a_message_row_carries_no_proposal_by_default() -> None:
    """``proposal`` is ``NULL`` on every 2A row — the column is purely additive."""
    path_id, lesson_id = await _arrange_path()

    async with db.async_session() as session:
        repository = ConversationRepository(session)
        conversation, _ = await repository.upsert_for_path(
            path_id, kind=ConversationKind.LESSON
        )
        await repository.insert_turn(
            conversation_id=conversation.id,
            lesson_id=lesson_id,
            learner_content="Why?",
            source=MessageSource.TYPED,
            tutor_content="Because.",
        )
        await session.commit()

    async with db.async_session() as session:
        proposals = list((await session.execute(select(Message.proposal))).scalars())
    assert proposals == [None, None]


# --------------------------------------------------------------------------- #
# Path-level shaping messages (AL-320, migration 0006)
#
# A shaping turn is about the path as a whole, so its rows carry no lesson. The
# thread query's lesson join is therefore **outer**: an inner one would return
# an empty shaping thread — a silently lost conversation rather than an error.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_shaping_turn_stores_no_lesson_and_still_loads() -> None:
    """``lesson_id=None`` round-trips, and the entry carries no lesson title."""
    path_id, _lesson_id = await _arrange_path()

    async with db.async_session() as session:
        repository = ConversationRepository(session)
        conversation, _created = await repository.upsert_for_path(
            path_id, kind=ConversationKind.SHAPING
        )
        await repository.insert_turn(
            conversation_id=conversation.id,
            lesson_id=None,
            learner_content="Add something on lifetimes.",
            source=MessageSource.TYPED,
            tutor_content="Here is what I would add.",
            proposal=PROPOSAL,
        )
        await session.commit()

    async with db.async_session() as session:
        thread = await ConversationRepository(session).load_thread(
            path_id, kind=ConversationKind.SHAPING
        )

    assert [entry.message.content for entry in thread] == [
        "Add something on lifetimes.",
        "Here is what I would add.",
    ]
    assert [entry.message.lesson_id for entry in thread] == [None, None]
    assert [entry.lesson_title for entry in thread] == [None, None]
    assert [entry.message.proposal for entry in thread] == [None, PROPOSAL]


@pytest.mark.anyio
async def test_an_in_lesson_turn_still_resolves_its_lesson_title() -> None:
    """The outer join changes nothing for a 2A row: same rows, same titles (W21)."""
    path_id, lesson_id = await _arrange_path()

    async with db.async_session() as session:
        repository = ConversationRepository(session)
        conversation, _created = await repository.upsert_for_path(
            path_id, kind=ConversationKind.LESSON
        )
        await repository.insert_turn(
            conversation_id=conversation.id,
            lesson_id=lesson_id,
            learner_content="Why does a move invalidate the source?",
            source=MessageSource.TYPED,
            tutor_content="Because ownership is unique.",
        )
        await session.commit()

    async with db.async_session() as session:
        thread = await ConversationRepository(session).load_thread(
            path_id, kind=ConversationKind.LESSON
        )

    assert [entry.lesson_title for entry in thread] == [
        "What ownership is",
        "What ownership is",
    ]
    assert [entry.message.lesson_id for entry in thread] == [lesson_id, lesson_id]
