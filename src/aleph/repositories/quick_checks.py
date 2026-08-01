"""Data access for quick checks (the single-select MCQ ending a lesson)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, select

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

    async def delete_for_lesson(self, lesson_id: uuid.UUID) -> None:
        """Drop a lesson's Quick check (a **Revision**'s reset, Phase 2B D7).

        Safe by the engagement boundary rather than by a guard: a lesson may
        only be revised while it is unengaged (D2), and an unengaged lesson has
        no **Attempt** — so this can never cascade away recorded work. The caller
        (``services/shaping.py``) proves that immediately before, inside the same
        transaction and under the per-path apply lock; the check is re-created
        from the Change's snapshot on undo.
        """
        await self.session.execute(
            delete(QuickCheck).where(QuickCheck.lesson_id == lesson_id)
        )
