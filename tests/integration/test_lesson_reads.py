"""Database transfer regressions: inspect executed SQL, not just DTO output."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import event

from aleph import db
from aleph.domains.progression import UnlockState
from aleph.models import Lesson, LessonGenerationState, Level, Path, QuickCheck, Unit
from aleph.repositories import LessonRepository
from aleph.services.lessons_read import lesson_unlock_state
from aleph.services.tutor_context import (
    LessonContextUnavailableError,
    assemble_lesson_context,
)

from .conftest import create_user

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def lesson_selects(isolated_database: str) -> Iterator[list[tuple[str, Any]]]:
    """Capture real lesson SELECTs without replacing the database or repository."""
    queries: list[tuple[str, Any]] = []

    def capture(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if statement.startswith("SELECT") and "\nFROM lessons" in statement:
            queries.append((statement, parameters))

    engine = db.engine.sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield queries
    finally:
        event.remove(engine, "before_cursor_execute", capture)


async def _seed() -> tuple[uuid.UUID, list[uuid.UUID]]:
    async with db.async_session() as session:
        user = await create_user(session, username=uuid.uuid4().hex)
        path = Path(user_id=user.id, topic="Ownership", level=Level.NEW_TO_IT)
        unit = Unit(path=path, position=1, title="Basics", summary="Ownership basics")
        session.add_all([path, unit])
        await session.flush()
        lessons = [
            Lesson(
                path_id=path.id,
                unit_id=unit.id,
                position_in_path=position,
                position_in_unit=position,
                title=f"Lesson {position}",
                generation_state=LessonGenerationState.GENERATED,
                read_passage=f"Passage {position}: " + "large body " * 1000,
                completed_at=datetime.now(UTC) if position == 1 else None,
            )
            for position in (1, 2, 3)
        ]
        # Insert out of order so an unordered read cannot satisfy progression.
        session.add_all(list(reversed(lessons)))
        await session.flush()
        session.add(
            QuickCheck(
                lesson_id=lessons[1].id,
                stem="Who owns it?",
                options=["First", "Second"],
                correct_index=1,
                explanation="It moved.",
            )
        )
        await session.commit()
        return path.id, [lesson.id for lesson in lessons]


@pytest.mark.anyio
async def test_unlock_reads_only_progress(
    lesson_selects: list[tuple[str, Any]],
) -> None:
    path_id, ids = await _seed()
    for lesson_id, expected in zip(
        [*ids, uuid.uuid4()],
        [UnlockState.COMPLETE, UnlockState.AVAILABLE, UnlockState.LOCKED, None],
        strict=True,
    ):
        async with db.async_session() as session:
            assert (
                await lesson_unlock_state(session, path_id=path_id, lesson_id=lesson_id)
                == expected
            )

    assert len(lesson_selects) == 4
    for statement, _ in lesson_selects:
        columns = statement.split("\nFROM")[0].removeprefix("SELECT ").strip()
        assert set(columns.split(", ")) == {
            "lessons.id",
            "lessons.position_in_path",
            "lessons.completed_at",
        }


@pytest.mark.anyio
async def test_outline_does_not_read_content(
    lesson_selects: list[tuple[str, Any]],
) -> None:
    path_id, ids = await _seed()
    async with db.async_session() as session:
        rows = await LessonRepository(session).list_for_path_with_effective_state(
            path_id
        )
        assert [lesson.id for lesson, _ in rows] == ids
        assert [lesson.title for lesson, _ in rows] == [
            "Lesson 1",
            "Lesson 2",
            "Lesson 3",
        ]
        assert all(state is LessonGenerationState.GENERATED for _, state in rows)

    assert len(lesson_selects) == 1
    statement, _ = lesson_selects[0]
    for column in ("read_passage", "revision_instruction", "generation_error"):
        assert f"lessons.{column}" not in statement


@pytest.mark.anyio
@pytest.mark.parametrize("summary_first", [False, True])
async def test_tutor_reads_only_current_passage(
    lesson_selects: list[tuple[str, Any]],
    summary_first: bool,
) -> None:
    path_id, ids = await _seed()
    async with db.async_session() as session:
        path = await session.get(Path, path_id)
        assert path is not None
        # Keep the partially loaded objects alive in the identity map: a plain
        # session.get() must not leave the current lesson's passage deferred.
        summaries = (
            await LessonRepository(session).list_for_path_with_effective_state(path_id)
            if summary_first
            else []
        )
        lesson_selects.clear()
        context = await assemble_lesson_context(session, path=path, lesson_id=ids[1])
        if summary_first:
            assert summaries[1][0].read_passage == context.deps.read_passage

    assert context.deps.read_passage == "Passage 2: " + "large body " * 1000
    assert [entry.unlock_state for entry in context.deps.path_digest] == [
        UnlockState.COMPLETE,
        UnlockState.AVAILABLE,
        UnlockState.LOCKED,
    ]
    assert len(lesson_selects) == 2
    content_reads = [q for q in lesson_selects if "lessons.read_passage" in q[0]]
    assert len(content_reads) == 1
    statement, parameters = content_reads[0]
    assert "WHERE lessons.id =" in statement
    assert ids[1] in parameters
    digest_statement = next(q[0] for q in lesson_selects if q not in content_reads)
    for column in ("read_passage", "revision_instruction", "generation_error"):
        assert f"lessons.{column}" not in digest_statement


@pytest.mark.anyio
async def test_tutor_rejects_an_existing_foreign_lesson() -> None:
    path_id, _ = await _seed()
    _, foreign_ids = await _seed()
    async with db.async_session() as session:
        path = await session.get(Path, path_id)
        assert path is not None
        with pytest.raises(LessonContextUnavailableError):
            await assemble_lesson_context(session, path=path, lesson_id=foreign_ids[1])
