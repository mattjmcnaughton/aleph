"""Schema integration tests against a real per-test Postgres database.

Covers the invariants AL-010 must guarantee (TDD §4): the ON DELETE CASCADE
chain tears down a whole path tree, the UNIQUE constraints reject duplicates,
and the enum columns round-trip as their CONTEXT.md/TDD state names.
"""

from __future__ import annotations

import uuid
from typing import cast

import pytest
from sqlalchemy import Table, delete, func, select, text
from sqlalchemy.exc import IntegrityError

from aleph import db
from aleph.models import (
    Attempt,
    Lesson,
    LessonGenerationState,
    Level,
    Path,
    PathStatus,
    QuickCheck,
    Unit,
    User,
)

from .conftest import create_user


async def _build_path_tree(
    session,
    *,
    user: User,
    topic: str = "Rust ownership",
) -> Path:
    """Build a full tree: path -> unit -> lesson -> quick check -> attempt."""
    path = Path(
        user_id=user.id,
        topic=topic,
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
    quick_check = QuickCheck(
        lesson=lesson,
        stem="What owns a value?",
        options=["A variable", "The heap", "The compiler"],
        correct_index=0,
        explanation="Each value has a single owning variable.",
    )
    attempt = Attempt(
        quick_check=quick_check,
        user_id=user.id,
        selected_index=0,
        is_correct=True,
    )
    session.add_all([path, unit, lesson, quick_check, attempt])
    await session.flush()
    return path


@pytest.mark.anyio
async def test_full_path_tree_round_trips() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _build_path_tree(session, user=user)
        await session.commit()
        path_id = path.id

    async with db.async_session() as session:
        saved = await session.get(Path, path_id)
        assert saved is not None
        assert saved.topic == "Rust ownership"
        assert saved.level is Level.SOME_EXPERIENCE
        assert saved.status is PathStatus.READY

        unit = (await session.execute(select(Unit))).scalar_one()
        assert unit.path_id == path_id
        assert unit.position == 1

        lesson = (await session.execute(select(Lesson))).scalar_one()
        assert lesson.path_id == path_id
        assert lesson.unit_id == unit.id
        assert lesson.generation_state is LessonGenerationState.GENERATED
        assert lesson.position_in_path == 1

        quick_check = (await session.execute(select(QuickCheck))).scalar_one()
        assert quick_check.lesson_id == lesson.id
        assert quick_check.options == ["A variable", "The heap", "The compiler"]
        assert quick_check.correct_index == 0

        attempt = (await session.execute(select(Attempt))).scalar_one()
        assert attempt.quick_check_id == quick_check.id
        assert attempt.is_correct is True


@pytest.mark.anyio
async def test_enum_columns_store_their_string_values() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = Path(
            user_id=user.id,
            topic="US healthcare",
            level=Level.NEW_TO_IT,
            status=PathStatus.PENDING,
        )
        session.add(path)
        await session.commit()
        path_id = path.id

    # The enum stores its value (not the Python member name): values_callable.
    async with db.async_session() as session:
        level = await session.scalar(
            text("SELECT level FROM paths WHERE id = :id"), {"id": path_id}
        )
        status = await session.scalar(
            text("SELECT status FROM paths WHERE id = :id"), {"id": path_id}
        )
    assert level == "new_to_it"
    assert status == "pending"


@pytest.mark.anyio
async def test_deleting_path_cascades_whole_tree() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = await _build_path_tree(session, user=user)
        await session.commit()
        path_id = path.id
        user_id = user.id

    # Core DELETE relies on the DB ON DELETE CASCADE chain, not ORM cascade.
    async with db.async_session() as session:
        await session.execute(delete(Path).where(Path.id == path_id))
        await session.commit()

    async with db.async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Unit)) == 0
        assert await session.scalar(select(func.count()).select_from(Lesson)) == 0
        assert await session.scalar(select(func.count()).select_from(QuickCheck)) == 0
        assert await session.scalar(select(func.count()).select_from(Attempt)) == 0
        # The learner account survives deleting one of their paths.
        assert await session.get(User, user_id) is not None


@pytest.mark.anyio
async def test_deleting_user_cascades_paths_and_attempts() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        await _build_path_tree(session, user=user)
        await session.commit()
        user_id = user.id

    async with db.async_session() as session:
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()

    async with db.async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Path)) == 0
        assert await session.scalar(select(func.count()).select_from(Unit)) == 0
        assert await session.scalar(select(func.count()).select_from(Lesson)) == 0
        assert await session.scalar(select(func.count()).select_from(QuickCheck)) == 0
        assert await session.scalar(select(func.count()).select_from(Attempt)) == 0


@pytest.mark.anyio
async def test_duplicate_issuer_subject_rejected() -> None:
    async with db.async_session() as session:
        await create_user(session, username="a", issuer="iss", subject="shared-subject")
        session.add(
            User(
                issuer="iss",
                subject="shared-subject",
                username="b",
                display_name="B",
                email=None,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.anyio
async def test_duplicate_unit_position_rejected() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = Path(user_id=user.id, topic="t", level=Level.NEW_TO_IT)
        session.add_all(
            [
                path,
                Unit(path=path, position=1, title="One", summary="s"),
                Unit(path=path, position=1, title="Two", summary="s"),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.anyio
async def test_duplicate_lesson_position_in_path_rejected() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = Path(user_id=user.id, topic="t", level=Level.NEW_TO_IT)
        unit = Unit(path=path, position=1, title="One", summary="s")
        session.add_all(
            [
                path,
                unit,
                Lesson(
                    unit=unit,
                    path=path,
                    position_in_path=1,
                    position_in_unit=1,
                    title="L1",
                ),
                Lesson(
                    unit=unit,
                    path=path,
                    position_in_path=1,
                    position_in_unit=2,
                    title="L2",
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.anyio
async def test_quick_check_is_one_to_one_with_lesson() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        path = Path(user_id=user.id, topic="t", level=Level.NEW_TO_IT)
        unit = Unit(path=path, position=1, title="One", summary="s")
        lesson = Lesson(
            unit=unit,
            path=path,
            position_in_path=1,
            position_in_unit=1,
            title="L1",
        )
        session.add_all(
            [
                path,
                unit,
                lesson,
                QuickCheck(
                    lesson=lesson,
                    stem="q1",
                    options=["a", "b", "c"],
                    correct_index=0,
                    explanation="e",
                ),
                QuickCheck(
                    lesson=lesson,
                    stem="q2",
                    options=["a", "b", "c"],
                    correct_index=1,
                    explanation="e",
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.anyio
async def test_one_attempt_per_quick_check_per_user_rejected() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        await _build_path_tree(session, user=user)
        await session.commit()
        quick_check_id = (await session.execute(select(QuickCheck.id))).scalar_one()
        user_id = user.id

    async with db.async_session() as session:
        session.add(
            Attempt(
                quick_check_id=quick_check_id,
                user_id=user_id,
                selected_index=1,
                is_correct=False,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.anyio
async def test_updated_at_is_populated() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        await session.commit()
        assert user.created_at is not None
        assert user.updated_at is not None
        assert isinstance(user.id, uuid.UUID)


@pytest.mark.anyio
async def test_the_streak_index_is_declared_on_both_model_and_migration() -> None:
    """Phase 5 TDD §4/D6: ``Lesson.__table_args__`` and migration ``0009`` agree.

    Migration ``0009`` is the only thing that actually creates
    ``ix_lessons_path_id_completed_at`` in a real database; the model's
    ``__table_args__`` is what any ORM-driven context (this suite's own
    ``Base.metadata``, a future script) would produce instead. Asserting both
    here is what keeps a hand-edit of one from silently drifting from the
    other — the property the migration's own docstring names this test for.
    """
    lessons_table = cast("Table", Lesson.__table__)
    model_index = next(
        index
        for index in lessons_table.indexes
        if index.name == "ix_lessons_path_id_completed_at"
    )
    assert [column.name for column in model_index.columns] == [
        "path_id",
        "completed_at",
    ]
    # A partial index (``postgresql_where``) — not a full one — is the whole
    # point (D6): most lessons on a growing path are incomplete.
    assert model_index.dialect_options["postgresql"]["where"] is not None

    async with db.async_session() as session:
        indexdef = await session.scalar(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'lessons' AND indexname = :name"
            ),
            {"name": "ix_lessons_path_id_completed_at"},
        )

    assert indexdef is not None
    assert "path_id" in indexdef
    assert "completed_at" in indexdef
    assert "completed_at IS NOT NULL" in indexdef
