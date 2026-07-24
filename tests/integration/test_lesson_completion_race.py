"""Path completion is derived race-safely (AL-070 advisory, thermo-1).

``path_completed`` (W3) must fire exactly once even if two lessons on the same path
are completed at the very same instant. The derivation lives in
``LessonRepository.mark_completed_and_finalize``, which locks the path row so the
"no lesson left incomplete" check serializes: only the completion that flips the
LAST incomplete lesson sees ``path_now_complete``. A naive non-atomic derivation
(mark, then count in independent transactions) would let both completions observe
zero remaining and both derive completion — a double emit. This drives the real
concurrency the HTTP route's progression gate otherwise hides (only one lesson is
ever "available" at a time), so the invariant is proven at its source.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from aleph import db
from aleph.models import Lesson as LessonModel
from aleph.models import LessonGenerationState, Level, Path, PathStatus, Unit
from aleph.repositories import LessonRepository

from .conftest import create_user

if TYPE_CHECKING:
    import uuid


async def _seed_two_incomplete_lessons() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A ready path with two generated, not-yet-completed lessons."""
    async with db.async_session() as session:
        user = await create_user(session)
        path = Path(
            user_id=user.id,
            topic="Race topic",
            level=Level.SOME_EXPERIENCE,
            status=PathStatus.READY,
        )
        session.add(path)
        await session.flush()
        unit = Unit(path=path, position=1, title="Unit 1", summary="s")
        session.add(unit)
        await session.flush()
        ids: list[uuid.UUID] = []
        for position in (1, 2):
            lesson = LessonModel(
                unit=unit,
                path=path,
                position_in_path=position,
                position_in_unit=position,
                title=f"Lesson {position}",
                generation_state=LessonGenerationState.GENERATED,
                read_passage=f"passage {position}",
                generated_at=datetime.now(UTC),
                completed_at=None,
            )
            session.add(lesson)
            await session.flush()
            ids.append(lesson.id)
        await session.commit()
        return path.id, ids[0], ids[1]


async def _complete(path_id: uuid.UUID, lesson_id: uuid.UUID) -> tuple[bool, bool, int]:
    async with db.async_session() as session:
        result = await LessonRepository(session).mark_completed_and_finalize(
            lesson_id=lesson_id, path_id=path_id
        )
        await session.commit()
        return result


@pytest.mark.anyio
@pytest.mark.workflow("W3")
async def test_concurrent_final_completions_finalize_once() -> None:
    path_id, first, second = await _seed_two_incomplete_lessons()

    results = await asyncio.gather(
        _complete(path_id, first),
        _complete(path_id, second),
    )

    # Both really completed their lesson...
    assert all(newly for newly, _, _ in results)
    # ...but exactly ONE completion derived path completion (no double path_completed).
    assert sum(1 for _, path_done, _ in results if path_done) == 1
    # And the lesson_count reported is the path total (count(), not a hydration).
    assert all(count == 2 for _, _, count in results)
