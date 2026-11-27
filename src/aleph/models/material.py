"""Material ORM models (CONTEXT.md: *Material*, *Material snapshot*).

A path's Material is read once, at creation, and stored here with the path in
the same transaction: the snapshot is frozen with the path (Constitution §3),
so nothing re-reads a link after the outline was built from it.
"""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aleph.db import Base
from aleph.models.base import UUIDAuditMixin

if TYPE_CHECKING:
    from aleph.models.path import Path


class Material(Base, UUIDAuditMixin):
    """One linked or pasted item attached to a path at creation."""

    __tablename__ = "materials"
    __table_args__ = (UniqueConstraint("path_id", "position"),)

    path_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("paths.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # ``domains.material.MaterialKind`` values; checked by the request DTO.
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # The link it was read from, or the link a transcript was pasted for.
    url: Mapped[str | None] = mapped_column(Text, nullable=True)

    path: Mapped[Path] = relationship(back_populates="materials")
    sections: Mapped[list[MaterialSection]] = relationship(
        back_populates="material",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="MaterialSection.position",
    )


class MaterialSection(Base, UUIDAuditMixin):
    """One section of a Material snapshot.

    ``position`` is numbered across the whole path (1..N), and is the id the
    outline's grounding plan names (``lessons.material_sections``).
    """

    __tablename__ = "material_sections"
    __table_args__ = (
        UniqueConstraint("path_id", "position"),
        Index("ix_material_sections_material_id", "material_id"),
    )

    material_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("materials.id", ondelete="CASCADE"), nullable=False
    )
    path_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("paths.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    material: Mapped[Material] = relationship(back_populates="sections")
