"""Atomically complete a lesson and recognize account-wide learning days."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from aleph.domains.milestones import Milestone, earned_milestones
from aleph.repositories import LessonRepository

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession


async def complete_with_milestones(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    lesson_id: uuid.UUID,
    path_id: uuid.UUID,
    tz_offset_minutes: int,
) -> tuple[bool, bool, int, list[Milestone]]:
    """Caller has authorized the owned, available lesson; commit one operation.

    Serialize completions per learner before the existing path lock: two paths
    must not both observe an empty day. Nothing besides completion is persisted.
    """
    lessons = LessonRepository(session)
    await lessons.serialize_completions_for_user(user_id)
    before = {
        row.day
        for row in await lessons.completion_days_for_user(
            user_id=user_id, tz_offset_minutes=tz_offset_minutes
        )
    }
    (
        newly_completed,
        path_now_complete,
        count,
    ) = await lessons.mark_completed_and_finalize(lesson_id=lesson_id, path_id=path_id)
    milestones: list[Milestone] = []
    if newly_completed:
        # Use the persisted transition's day, not a second wall-clock read:
        # a completion queued over midnight must agree with completed_at.
        stamp = await lessons.completion_instant(lesson_id)
        assert stamp is not None  # guarded mark succeeded in this transaction
        day = (stamp - timedelta(minutes=tz_offset_minutes)).date()
        milestones = earned_milestones(before, today=day)
    await session.commit()
    return newly_completed, path_now_complete, count, milestones
