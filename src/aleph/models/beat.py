"""Beat ORM model (CONTEXT.md: Beat — a standing research assignment)."""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from datetime import (
    datetime,  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
)

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from aleph.db import Base
from aleph.models.base import UUIDAuditMixin
from aleph.models.enums import BeatResearchState, Level


class Beat(Base, UUIDAuditMixin):
    """A standing research assignment on one Topic at one Level (CONTEXT.md).

    The top-level sibling of a Path — deliberately not an extension of
    ``paths`` (Phase 6 TDD D1): a Beat has none of the four ``NOT NULL``
    columns a Brief could never fill (``unit_id``, ``path_id``,
    ``position_in_path``, ``position_in_unit``). Carries its own claim
    protocol exactly as ``paths`` carries the outline claim (D3):
    ``research_state`` (idle -> researching -> idle, with failed/refused
    branches), ``research_started_at`` (the claim fence),
    ``research_error``, ``refusal_message`` — claimed by
    ``repositories/beats.py`` through ``repositories/_generation.py``'s
    ``claimable_predicate``/``stale_cutoff``, imported unchanged.

    ``topic``/``guidance``/``level`` are frozen generation inputs, the
    ``paths.topic``/``paths.guidance``/``paths.level`` precedent — no route
    ever writes them after ``create`` (changing your mind means delete and
    redeploy, CONTEXT.md: Beat). ``anchor_weekday`` is CONTEXT.md's Anchor
    day, Python's ``Monday == 0`` convention, bounded 0..6 by
    ``ck_beats_anchor_weekday_range``.

    Deliberately carries **no** ``title`` (TDD §4: nothing in the PRD asks to
    rename a Beat, the Topic is the label) and **no** ``next_claimable_at``
    (D4: derived by ``domains/cadence.py`` from the Briefs rail's
    ``max(published_on)``, never stored). Also carries **no**
    ``relationship()`` attributes, the ``models/flashcard.py`` precedent:
    every read this phase needs is a repository-level ``select`` scoped by
    ``user_id``/``beat_id`` on the row itself, and this module does not touch
    ``User``.
    """

    __tablename__ = "beats"
    __table_args__ = (
        Index("ix_beats_user_id", "user_id"),
        # Python's Monday == 0 (CONTEXT.md: Anchor day), enforced at the
        # storage layer so an out-of-range weekday can never be written no
        # matter what validation a service does or skips.
        CheckConstraint(
            "anchor_weekday >= 0 AND anchor_weekday <= 6",
            name="ck_beats_anchor_weekday_range",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The generation input (CONTEXT.md: Topic), frozen once the Beat exists —
    # every research/analyst prompt reads it (``paths.topic``'s precedent).
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    # The learner's optional free text (CONTEXT.md: Guidance), frozen as
    # ``paths.guidance`` is.
    guidance: Mapped[str | None] = mapped_column(Text, nullable=True)
    level: Mapped[Level] = mapped_column(
        Enum(
            Level,
            name="level",
            values_callable=lambda enum: [e.value for e in enum],
        ),
        nullable=False,
    )
    anchor_weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    research_state: Mapped[BeatResearchState] = mapped_column(
        Enum(
            BeatResearchState,
            name="beat_research_state",
            values_callable=lambda enum: [e.value for e in enum],
        ),
        nullable=False,
        default=BeatResearchState.IDLE,
    )
    research_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    research_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    refusal_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Admin model-picker overrides (TDD D7, §5.3), the ``paths.model_outline``/
    # ``paths.model_lesson`` precedent: stored on the row (not held on the
    # request) so the DB-driven claim/retry re-runs research with the model an
    # admin chose. ``None`` means "use the configured slot".
    model_research: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_brief: Mapped[str | None] = mapped_column(Text, nullable=True)
