"""Data access for units (the ordered lesson groupings within a path)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

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

    async def list_for_path(self, path_id: uuid.UUID) -> list[Unit]:
        """A path's units in display (``position``) order."""
        result = await self.session.execute(
            select(Unit).where(Unit.path_id == path_id).order_by(Unit.position)
        )
        return list(result.scalars())
