"""Data access for units (the ordered lesson groupings within a path)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select, update

from aleph.models import Unit

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession


class UnitRepository:
    """Data access for :class:`~aleph.models.Unit` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, *, path_id: uuid.UUID, position: int, title: str, summary: str
    ) -> Unit:
        unit = Unit(path_id=path_id, position=position, title=title, summary=summary)
        self.session.add(unit)
        await self.session.flush()
        return unit

    async def get(self, unit_id: uuid.UUID) -> Unit | None:
        return await self.session.get(Unit, unit_id)

    async def list_for_path(self, path_id: uuid.UUID) -> list[Unit]:
        """A path's units in display (``position``) order."""
        result = await self.session.execute(
            select(Unit).where(Unit.path_id == path_id).order_by(Unit.position)
        )
        return list(result.scalars())

    # -- shaping: apply / undo writes (Phase 2B §5.6/§5.7) ------------------ #

    async def move_to_position(self, *, unit_id: uuid.UUID, position: int) -> None:
        """Set one unit's display position.

        ``UNIQUE (path_id, position)`` is non-deferrable here too, so an
        **Addition** that creates a new unit renumbers the path's units one row
        at a time through a scratch range (``services/shaping.py``) rather than
        with a set-based bump.
        """
        await self.session.execute(
            update(Unit)
            .where(Unit.id == unit_id)
            .values(position=position, updated_at=func.now())
        )

    async def delete(self, unit_id: uuid.UUID) -> None:
        """Remove one unit (undo of an **Addition** that created it).

        Its lessons cascade — but undo has already deleted them explicitly, and
        a unit the learner has since had lessons added to is not a unit this
        Change created, so there is nothing here that can take a live lesson
        with it.
        """
        await self.session.execute(delete(Unit).where(Unit.id == unit_id))
