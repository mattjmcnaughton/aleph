"""Data access for lesson visuals (CONTEXT.md: **Visual**)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from aleph.models import LessonVisual, LessonVisualKind

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession


class LessonVisualRepository:
    """Data access for :class:`~aleph.models.LessonVisual` rows.

    Never commits: a visual is written, deleted and restored inside the same
    transaction as its lesson's Read passage (generation, Revision, Undo).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        visual_id: uuid.UUID,
        lesson_id: uuid.UUID,
        title: str,
        alt: str,
        source: str,
        kind: LessonVisualKind = LessonVisualKind.INTERACTIVE,
        kit_version: int = 1,
    ) -> LessonVisual:
        """Insert one visual under a caller-chosen id.

        The id is the caller's because the passage fence must name it before
        the passage is written (generation), and because Undo restores a visual
        under the id its restored passage already references.
        """
        visual = LessonVisual(
            id=visual_id,
            lesson_id=lesson_id,
            kind=kind,
            title=title,
            alt=alt,
            source=source,
            kit_version=kit_version,
        )
        self.session.add(visual)
        await self.session.flush()
        return visual

    async def list_for_lesson(self, lesson_id: uuid.UUID) -> list[LessonVisual]:
        result = await self.session.execute(
            select(LessonVisual)
            .where(LessonVisual.lesson_id == lesson_id)
            .order_by(LessonVisual.created_at, LessonVisual.id)
        )
        return list(result.scalars())

    async def delete_for_lesson(self, lesson_id: uuid.UUID) -> None:
        """Drop a lesson's visuals (a **Revision**'s reset, or Undo's replace)."""
        await self.session.execute(
            delete(LessonVisual).where(LessonVisual.lesson_id == lesson_id)
        )
