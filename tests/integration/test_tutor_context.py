"""``assemble_lesson_context`` against a real Postgres database (TDD §5.2, D6).

The seam is defined by what it reads and by what it must **not** do, and both
only mean something against real rows:

* **Deps** — the current lesson's scope (topic/level, unit and lesson titles,
  position, Read passage, Quick check with its keyed answer) plus the caller's
  Attempt if any, re-graded from the stored ``selected_index`` rather than the
  ``attempts.is_correct`` denormalization (the Phase 1 discipline, AL-012).
* **Digest** — every lesson on the path as unit/lesson **names** with the state
  ``domains/progression.derive_unlock_states`` derives (PRD §5.2: names and
  state only, never another lesson's body).
* **History** — the most recent ``TUTOR_CONTEXT_TURNS`` turns of the *stored*
  thread, oldest first, with a prior Tutor check serialized as text.
* **Purity** — no writes and no generation triggers. The seam builds the digest
  from ``derive_unlock_states``, not ``load_path_detail``, precisely because the
  read seams poll-as-trigger: a chat turn must not claim an ungenerated lesson
  for generation. That is asserted here by fingerprinting the database either side
  of an assembly that commits its session.

Turn pairing, the window arithmetic and the Tutor-check text form are pure and
covered in ``tests/unit/test_tutor_context.py``; this module proves they compose
over rows the repositories actually return.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import func, select

from aleph import db
from aleph.agents.tutor import (
    CURRENT_LESSON_BLOCK,
    PATH_DIGEST_BLOCK,
    POST_ATTEMPT_RULE,
    render_lesson_context,
)
from aleph.config import settings
from aleph.domains.grading import Outcome
from aleph.domains.progression import UnlockState
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
    QuickCheck,
    Unit,
    User,
)
from aleph.repositories import ConversationRepository
from aleph.services.tutor_context import (
    LessonContextUnavailableError,
    assemble_lesson_context,
)

from .conftest import create_user

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.services.tutor_context import AssembledContext

STEM = "Which binding owns the String after a move?"
OPTIONS = ["The first", "The second", "Neither"]
CORRECT_INDEX = 1
EXPLANATION = "A move transfers ownership to the new binding."
PASSAGE = "Ownership is Rust's memory model: one owner at a time."

TUTOR_CHECK: dict[str, Any] = {
    "stem": "What happens to the source binding?",
    "options": ["It is invalidated", "It is copied", "It is borrowed"],
    "correct_index": 0,
    "explanation": "A move leaves the source unusable.",
    "answered_index": None,
}


# --------------------------------------------------------------------------- #
# Arrange helpers
#
# One path, two units, four lessons in ``position_in_path`` order:
#   1 complete, 2 generated (the lesson the learner is reading), 3 and 4 ungenerated.
# That shape gives the digest one of each unlock state and leaves two
# *ungenerated* lessons a poll-as-trigger read would have claimed for generation.
# --------------------------------------------------------------------------- #


async def _arrange() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Commit the fixture path; return ``(user_id, path_id, lesson_id)``."""
    async with db.async_session() as session:
        user = await create_user(session)
        path = Path(user_id=user.id, topic="Rust ownership", level=Level.NEW_TO_IT)
        session.add(path)
        await session.flush()

        foundations = Unit(
            path=path, position=1, title="Foundations", summary="The basics"
        )
        borrowing = Unit(path=path, position=2, title="Borrowing", summary="References")
        session.add_all([foundations, borrowing])
        await session.flush()

        first = _lesson(
            path,
            foundations,
            position=1,
            title="What ownership is",
            state=LessonGenerationState.GENERATED,
            completed=True,
        )
        current = _lesson(
            path,
            foundations,
            position=2,
            title="Moves and copies",
            state=LessonGenerationState.GENERATED,
        )
        third = _lesson(
            path,
            borrowing,
            position=3,
            title="Shared references",
            state=LessonGenerationState.UNGENERATED,
        )
        fourth = _lesson(
            path,
            borrowing,
            position=4,
            title="Mutable references",
            state=LessonGenerationState.UNGENERATED,
        )
        session.add_all([first, current, third, fourth])
        await session.flush()

        session.add(
            QuickCheck(
                lesson_id=current.id,
                stem=STEM,
                options=OPTIONS,
                correct_index=CORRECT_INDEX,
                explanation=EXPLANATION,
            )
        )
        await session.commit()
        return user.id, path.id, current.id


def _lesson(
    path: Path,
    unit: Unit,
    *,
    position: int,
    title: str,
    state: LessonGenerationState,
    completed: bool = False,
) -> Lesson:
    return Lesson(
        unit=unit,
        path=path,
        position_in_path=position,
        position_in_unit=position,
        title=title,
        generation_state=state,
        read_passage=PASSAGE if state is LessonGenerationState.GENERATED else None,
        completed_at=datetime.now(UTC) if completed else None,
    )


async def _owned_path(session: AsyncSession, path_id: uuid.UUID) -> Path:
    """The ``Path`` row the router hands the seam (its ownership check's result)."""
    path = await session.get(Path, path_id)
    assert path is not None
    return path


async def _lesson_id_at(path_id: uuid.UUID, position: int) -> uuid.UUID:
    """The fixture path's lesson at ``position_in_path`` (1-4)."""
    async with db.async_session() as session:
        lesson_id = await session.scalar(
            select(Lesson.id).where(
                Lesson.path_id == path_id, Lesson.position_in_path == position
            )
        )
    assert lesson_id is not None
    return lesson_id


async def _record_turns(path_id: uuid.UUID, lesson_id: uuid.UUID, count: int) -> None:
    async with db.async_session() as session:
        repository = ConversationRepository(session)
        conversation, _created = await repository.upsert_for_path(
            path_id, kind=ConversationKind.LESSON
        )
        for index in range(1, count + 1):
            await repository.insert_turn(
                conversation_id=conversation.id,
                lesson_id=lesson_id,
                learner_content=f"question {index}",
                source=MessageSource.TYPED,
                tutor_content=f"reply {index}",
            )
        await session.commit()


# --------------------------------------------------------------------------- #
# Deps: the current lesson's scope
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_deps_carry_the_current_lessons_scope() -> None:
    _user_id, path_id, lesson_id = await _arrange()

    async with db.async_session() as session:
        context = await assemble_lesson_context(
            session, path=await _owned_path(session, path_id), lesson_id=lesson_id
        )

    deps = context.deps
    assert deps.topic == "Rust ownership"
    assert deps.level == "beginner"  # Level.NEW_TO_IT, mapped to the agent vocabulary
    assert deps.unit_title == "Foundations"
    assert deps.lesson_title == "Moves and copies"
    assert deps.position_in_path == 2  # noqa: PLR2004 - the fixture's second lesson
    assert deps.read_passage == PASSAGE
    assert deps.quick_check.stem == STEM
    assert deps.quick_check.options == OPTIONS
    assert deps.quick_check.correct_index == CORRECT_INDEX
    assert deps.quick_check.explanation == EXPLANATION


@pytest.mark.anyio
async def test_a_lesson_on_another_path_is_rejected() -> None:
    """The router validates first (§5.5 step 1); a mismatch here is a raced delete."""
    _user_id, path_id, _lesson_id = await _arrange()

    async with db.async_session() as session:
        with pytest.raises(LookupError):
            await assemble_lesson_context(
                session,
                path=await _owned_path(session, path_id),
                lesson_id=uuid.uuid4(),
            )


@pytest.mark.anyio
async def test_an_ungenerated_lesson_cannot_ground_a_turn() -> None:
    """§5.5 step 1's other half: on the path, but with no generated content."""
    _user_id, path_id, _lesson_id = await _arrange()
    ungenerated = await _lesson_id_at(path_id, 3)

    async with db.async_session() as session:
        with pytest.raises(LessonContextUnavailableError):
            await assemble_lesson_context(
                session,
                path=await _owned_path(session, path_id),
                lesson_id=ungenerated,
            )


# --------------------------------------------------------------------------- #
# The path digest (AC: reflects `derive_unlock_states`; names and state only)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_digest_reflects_derived_unlock_states() -> None:
    _user_id, path_id, lesson_id = await _arrange()

    async with db.async_session() as session:
        context = await assemble_lesson_context(
            session, path=await _owned_path(session, path_id), lesson_id=lesson_id
        )

    assert [
        (entry.unit_title, entry.lesson_title, entry.unlock_state)
        for entry in context.deps.path_digest
    ] == [
        ("Foundations", "What ownership is", UnlockState.COMPLETE),
        ("Foundations", "Moves and copies", UnlockState.AVAILABLE),
        ("Borrowing", "Shared references", UnlockState.LOCKED),
        ("Borrowing", "Mutable references", UnlockState.LOCKED),
    ]


@pytest.mark.anyio
async def test_digest_carries_no_other_lessons_body() -> None:
    """PRD §5.2: names and state only — a `DigestEntry` has nowhere to put more."""
    _user_id, path_id, lesson_id = await _arrange()

    async with db.async_session() as session:
        context = await assemble_lesson_context(
            session, path=await _owned_path(session, path_id), lesson_id=lesson_id
        )

    rendered = render_lesson_context(context.deps)
    digest_block = rendered.split(f"<{PATH_DIGEST_BLOCK}>")[1].split(
        f"</{PATH_DIGEST_BLOCK}>"
    )[0]
    assert PASSAGE not in digest_block
    assert STEM not in digest_block


# --------------------------------------------------------------------------- #
# The caller's Attempt
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_no_attempt_before_the_learner_attempts() -> None:
    _user_id, path_id, lesson_id = await _arrange()

    async with db.async_session() as session:
        context = await assemble_lesson_context(
            session, path=await _owned_path(session, path_id), lesson_id=lesson_id
        )

    assert context.deps.attempt is None


@pytest.mark.anyio
async def test_attempt_outcome_is_regraded_never_read_from_is_correct() -> None:
    user_id, path_id, lesson_id = await _arrange()
    # ``is_correct`` deliberately disagrees with the keyed answer: the seam must
    # re-derive the Outcome from ``selected_index`` (AL-012 / domains/grading).
    await _record_attempt(lesson_id, user_id=user_id, selected_index=0, stored=True)

    async with db.async_session() as session:
        context = await assemble_lesson_context(
            session, path=await _owned_path(session, path_id), lesson_id=lesson_id
        )

    assert context.deps.attempt is not None
    assert context.deps.attempt.selected_index == 0
    assert context.deps.attempt.outcome is Outcome.INCORRECT
    # And the deps compose: an Attempt on them is what selects the post-Attempt
    # regime when the agent renders its prompt from this seam's output.
    assert POST_ATTEMPT_RULE in render_lesson_context(context.deps)


@pytest.mark.anyio
async def test_another_learners_attempt_is_not_carried() -> None:
    """The caller is the path's owner — someone else's Attempt is invisible."""
    _user_id, path_id, lesson_id = await _arrange()
    async with db.async_session() as session:
        other = await create_user(session, username="someone-else")
        await session.commit()
        other_id = other.id
    await _record_attempt(lesson_id, user_id=other_id, selected_index=CORRECT_INDEX)

    async with db.async_session() as session:
        context = await assemble_lesson_context(
            session, path=await _owned_path(session, path_id), lesson_id=lesson_id
        )

    assert context.deps.attempt is None


async def _record_attempt(
    lesson_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    selected_index: int,
    stored: bool = False,
) -> None:
    async with db.async_session() as session:
        quick_check_id = await session.scalar(
            select(QuickCheck.id).where(QuickCheck.lesson_id == lesson_id)
        )
        session.add(
            Attempt(
                quick_check_id=quick_check_id,
                user_id=user_id,
                selected_index=selected_index,
                is_correct=stored,
            )
        )
        await session.commit()


# --------------------------------------------------------------------------- #
# History: the stored thread, windowed
# --------------------------------------------------------------------------- #


def _texts(context: AssembledContext) -> list[str]:
    return [
        content
        for message in context.message_history
        for part in message.parts
        if isinstance(content := getattr(part, "content", None), str)
    ]


@pytest.mark.anyio
async def test_empty_thread_yields_empty_history() -> None:
    """No conversation row at all — the first turn of a path."""
    _user_id, path_id, lesson_id = await _arrange()

    async with db.async_session() as session:
        context = await assemble_lesson_context(
            session, path=await _owned_path(session, path_id), lesson_id=lesson_id
        )

    assert context.message_history == []


@pytest.mark.anyio
async def test_history_carries_the_configured_window_oldest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user_id, path_id, lesson_id = await _arrange()
    monkeypatch.setattr(settings, "tutor_context_turns", 3)
    await _record_turns(path_id, lesson_id, count=5)

    async with db.async_session() as session:
        context = await assemble_lesson_context(
            session, path=await _owned_path(session, path_id), lesson_id=lesson_id
        )

    assert _texts(context) == [
        "question 3",
        "reply 3",
        "question 4",
        "reply 4",
        "question 5",
        "reply 5",
    ]


@pytest.mark.anyio
async def test_turns_recorded_under_another_lesson_are_carried() -> None:
    """The conversation is *path*-scoped: history spans the path's lessons."""
    _user_id, path_id, lesson_id = await _arrange()
    await _record_turns(path_id, await _lesson_id_at(path_id, 1), count=1)

    async with db.async_session() as session:
        context = await assemble_lesson_context(
            session, path=await _owned_path(session, path_id), lesson_id=lesson_id
        )

    assert _texts(context) == ["question 1", "reply 1"]


@pytest.mark.anyio
async def test_a_prior_tutor_check_rides_as_text_with_the_learners_answer() -> None:
    _user_id, path_id, lesson_id = await _arrange()
    async with db.async_session() as session:
        repository = ConversationRepository(session)
        conversation, _created = await repository.upsert_for_path(
            path_id, kind=ConversationKind.LESSON
        )
        await repository.insert_turn(
            conversation_id=conversation.id,
            lesson_id=lesson_id,
            learner_content="quiz me",
            source=MessageSource.SUGGESTION,
            tutor_content="Here you go.",
            tutor_check={**TUTOR_CHECK, "answered_index": 2},
        )
        await session.commit()

    async with db.async_session() as session:
        context = await assemble_lesson_context(
            session, path=await _owned_path(session, path_id), lesson_id=lesson_id
        )

    reply = _texts(context)[1]
    assert reply.startswith("Here you go.")
    assert TUTOR_CHECK["stem"] in reply
    assert "Correct option index: 0" in reply
    assert "Learner's answer index: 2" in reply
    # §5.1: text, never tool parts — a SystemPromptPart would restate a stale
    # Attempt regime, and adapters vary in how they map a tool call with no
    # matching tool return.
    kinds = {
        type(part).__name__
        for message in context.message_history
        for part in message.parts
    }
    assert kinds == {"UserPromptPart", "TextPart"}


@pytest.mark.anyio
async def test_the_lesson_block_rides_in_instructions_not_in_history() -> None:
    """§5.2's budget shape: the lesson block is re-sent every turn, ordered last.

    History is the windowed turns and nothing else, so a long thread cannot
    crowd the lesson out — it is not competing for the same slot.
    """
    _user_id, path_id, lesson_id = await _arrange()
    await _record_turns(path_id, lesson_id, count=2)

    async with db.async_session() as session:
        context = await assemble_lesson_context(
            session, path=await _owned_path(session, path_id), lesson_id=lesson_id
        )

    assert all(PASSAGE not in text for text in _texts(context))
    rendered = render_lesson_context(context.deps)
    assert rendered.index(f"<{PATH_DIGEST_BLOCK}>") < rendered.index(
        f"<{CURRENT_LESSON_BLOCK}>"
    )


# --------------------------------------------------------------------------- #
# Purity (AC: no writes, no generation triggers)
# --------------------------------------------------------------------------- #


async def _fingerprint() -> tuple[Any, ...]:
    """Everything a write or a poll-as-trigger read would disturb."""
    async with db.async_session() as session:
        lessons = (
            await session.execute(
                select(
                    Lesson.id,
                    Lesson.generation_state,
                    Lesson.generation_started_at,
                    Lesson.generated_at,
                    Lesson.completed_at,
                    Lesson.updated_at,
                ).order_by(Lesson.position_in_path)
            )
        ).all()
        paths = (
            await session.execute(
                select(Path.status, Path.generation_started_at, Path.updated_at)
            )
        ).all()
        counts = []
        for model in (
            User,
            Path,
            Unit,
            Lesson,
            QuickCheck,
            Attempt,
            Conversation,
            Message,
        ):
            counts.append(await session.scalar(select(func.count()).select_from(model)))
        return (lessons, paths, tuple(counts))


@pytest.mark.anyio
async def test_assembly_writes_nothing_and_triggers_no_generation() -> None:
    """No state change — the reason the digest is not built from `load_path_detail`.

    Two *ungenerated* lessons sit on the fixture path: the paths/lessons read seams
    would poll-as-trigger and claim them for generation (stamping
    ``generation_started_at``). The seam's session is committed here, so an
    accidental write would land rather than being rolled back.
    """
    _user_id, path_id, lesson_id = await _arrange()
    await _record_turns(path_id, lesson_id, count=2)
    before = await _fingerprint()

    async with db.async_session() as session:
        await assemble_lesson_context(
            session, path=await _owned_path(session, path_id), lesson_id=lesson_id
        )
        assert not session.new
        assert not session.dirty
        assert not session.deleted
        await session.commit()

    assert await _fingerprint() == before
