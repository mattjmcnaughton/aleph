"""Material snapshot storage (CONTEXT.md: *Material snapshot*).

Written once, in the transaction that creates the path, and only read after.
Callers reach a path's Material through a path they already own.
"""

from __future__ import annotations

import uuid  # noqa: TC003 - used at runtime in query parameters.
from collections.abc import Sequence  # noqa: TC003
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from aleph.models import Material, MaterialSection


@dataclass(frozen=True)
class NewSection:
    title: str
    text: str


@dataclass(frozen=True)
class NewMaterial:
    kind: str
    title: str
    url: str | None
    sections: Sequence[NewSection]


@dataclass(frozen=True)
class SectionRow:
    """A section with its Material's title, in path-wide ``position`` order."""

    position: int
    material_title: str
    title: str
    text: str


class MaterialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_snapshot(
        self, path_id: uuid.UUID, materials: Sequence[NewMaterial]
    ) -> None:
        """Insert every item and its sections, numbering sections 1..N path-wide."""
        position = 0
        for index, item in enumerate(materials, start=1):
            material = Material(
                path_id=path_id,
                position=index,
                kind=item.kind,
                title=item.title,
                url=item.url,
            )
            self.session.add(material)
            await self.session.flush()
            for section in item.sections:
                position += 1
                self.session.add(
                    MaterialSection(
                        material_id=material.id,
                        path_id=path_id,
                        position=position,
                        title=section.title,
                        text=section.text,
                    )
                )
        await self.session.flush()

    async def list_for_path(self, path_id: uuid.UUID) -> list[Material]:
        result = await self.session.execute(
            select(Material)
            .where(Material.path_id == path_id)
            .order_by(Material.position)
        )
        return list(result.scalars())

    async def sections(
        self, path_id: uuid.UUID, positions: Sequence[int] | None = None
    ) -> list[SectionRow]:
        """The path's sections, or only those at ``positions``, in order."""
        query = (
            select(MaterialSection, Material.title)
            .join(Material, MaterialSection.material_id == Material.id)
            .where(MaterialSection.path_id == path_id)
            .order_by(MaterialSection.position)
        )
        if positions is not None:
            query = query.where(MaterialSection.position.in_(list(positions)))
        result = await self.session.execute(query)
        return [
            SectionRow(
                position=section.position,
                material_title=material_title,
                title=section.title,
                text=section.text,
            )
            for section, material_title in result.all()
        ]
