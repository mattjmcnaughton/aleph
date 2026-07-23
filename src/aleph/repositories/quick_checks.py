"""Data access for quick checks (the single-select MCQ ending a lesson)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from aleph.models import QuickCheck

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession


class QuickCheckRepository:
    """Data access for :class:`~aleph.models.QuickCheck` rows (1:1 with a lesson)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        lesson_id: uuid.UUID,
        stem: str,
        options: list[str],
        correct_index: int,
        explanation: str,
    ) -> QuickCheck:
        quick_check = QuickCheck(
            lesson_id=lesson_id,
            stem=stem,
            options=options,
            correct_index=correct_index,
            explanation=explanation,
        )
        self.session.add(quick_check)
        await self.session.flush()
        return quick_check

    async def get_for_lesson(self, lesson_id: uuid.UUID) -> QuickCheck | None:
        result = await self.session.execute(
            select(QuickCheck).where(QuickCheck.lesson_id == lesson_id)
        )
        return result.scalar_one_or_none()
