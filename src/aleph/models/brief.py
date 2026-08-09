"""Brief and Source ORM models (CONTEXT.md: Brief, Source)."""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from datetime import (  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
    date,
    datetime,
)

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from aleph.db import Base
from aleph.models.base import UUIDAuditMixin
from aleph.models.enums import BriefKind


class Brief(Base, UUIDAuditMixin):
    """One dated, cited report published by a Beat — or a Skipped period (D2).

    A discriminated row rather than two tables (Phase 6 TDD D1/D2): ``kind =
    'published'`` is a numbered, titled, bodied report; ``kind = 'skipped'``
    is a dated, unnumbered, one-line entry with no body and no Sources.
    ``number`` is therefore sparse — ``NULL`` on a Skipped row — under the
    partial ``uq_briefs_beat_id_number`` index below, which is what lets two
    Skipped rows share one Beat: the ``UNIQUE`` never sees two ``NULL``s
    because a partial index simply does not index them.

    **The two ``CHECK`` constraints make D2 structural, not conventional**
    (TDD §4): a padded Brief cannot be written as a Skipped row and a Skipped
    period cannot acquire a body, at the storage layer, whatever any service
    does — PRD §4.6 names its own rule the one most likely to be argued away
    later, and this is the cheapest place to make that argument cost a
    migration.

    ``claims`` is a Postgres array, not a table (TDD §4): it is only ever
    read as a whole set for one Beat (D9's novelty-gate input), never queried
    element-wise and never indexed, so a table would buy a join and no
    capability. ``read_at``/``sources_seen_at`` are first-write-wins columns
    (D11, §6) — ``repositories/briefs.py`` guards both with the same ``WHERE
    ... IS NULL`` shape ``LessonRepository.mark_completed`` uses, and for the
    same reason: the north-star metric asks when a learner *first* opened a
    Brief, so a re-read must never move the timestamp.

    Deliberately carries **no** ``builds_on_brief_id`` (TDD §4): "Builds on
    Brief #4" is the highest-numbered published Brief below this one — a
    ``WHERE number < :n ORDER BY number DESC LIMIT 1`` read, not a stored edge
    that could disagree with the numbering. Carries **no**
    ``relationship()`` attributes either, the ``models/flashcard.py``
    precedent — every read this phase needs is a repository-level ``select``
    scoped by ``beat_id`` on the row itself.
    """

    __tablename__ = "briefs"
    __table_args__ = (
        # Sparse over published Briefs only (D2): a Skipped row's NULL number
        # is invisible to this index, so any number of Skipped rows may share
        # a Beat — the property ``test_two_skipped_briefs_in_one_beat_succeed``
        # exists to pin.
        Index(
            "uq_briefs_beat_id_number",
            "beat_id",
            "number",
            unique=True,
            postgresql_where=text("number IS NOT NULL"),
        ),
        # The rail read (§4): ``WHERE beat_id = ? ORDER BY published_on DESC``,
        # both kinds interleaved.
        Index(
            "ix_briefs_beat_id_published_on",
            "beat_id",
            text("published_on DESC"),
        ),
        CheckConstraint(
            "kind <> 'published' OR (number IS NOT NULL AND title IS NOT NULL "
            "AND body_markdown IS NOT NULL AND skip_line IS NULL)",
            name="ck_briefs_published_shape",
        ),
        CheckConstraint(
            "kind <> 'skipped' OR (number IS NULL AND body_markdown IS NULL "
            "AND skip_line IS NOT NULL)",
            name="ck_briefs_skipped_shape",
        ),
    )

    beat_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("beats.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[BriefKind] = mapped_column(
        Enum(
            BriefKind,
            name="brief_kind",
            values_callable=lambda enum: [e.value for e in enum],
        ),
        nullable=False,
    )
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    published_on: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    skip_line: Mapped[str | None] = mapped_column(Text, nullable=True)
    claims: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sources_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class BriefSource(Base, UUIDAuditMixin):
    """A retrieved document a Brief cites (CONTEXT.md: Source).

    A table, not a JSONB blob on ``briefs`` (Phase 6 TDD D1, amending the
    two-table draft): D9's novelty gate reads **prior cited Source URLs**
    across a Beat's *whole history*, which a JSONB column would make an
    unnest on every read. Rows are rendered individually with four
    structured fields (``publisher``, ``title``, ``published_on``, ``url``),
    the opposite shape from ``briefs.claims`` above, which is read only as a
    whole set.

    A Source's metadata is never model-written (TDD §5.5): the writing agent
    emits URLs only, and ``publisher``/``title``/``published_on`` are joined
    from the ``RetrievedDocument`` the retriever returned before a row here
    is materialized — out of this ticket's scope (``services/briefing.py``),
    named here so the columns' provenance is on record.
    """

    __tablename__ = "brief_sources"
    __table_args__ = (
        UniqueConstraint(
            "brief_id", "position", name="uq_brief_sources_brief_id_position"
        ),
        Index("ix_brief_sources_brief_id", "brief_id"),
    )

    brief_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("briefs.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    publisher: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    published_on: Mapped[date] = mapped_column(Date, nullable=False)
