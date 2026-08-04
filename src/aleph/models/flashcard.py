"""Flashcard ORM models (Phase 3 TDD §4: Flashcard, FlashcardReview, FlashcardDraftRun).

Three tables, migration ``0010``. D1's whole shape lands here: ``flashcard_reviews``
is append-only and authoritative; ``Flashcard.rung``/``Flashcard.due_on`` is a
projection over it, written in the same transaction as the review row
(:meth:`aleph.repositories.flashcards.FlashcardRepository.append_review_and_project`)
and rebuildable in full by replaying the log through the same pure ladder
(:mod:`aleph.domains.scheduling`, owned by a concurrent ticket — nothing here
imports it).

Deliberately carries **no** ``relationship()`` attributes: every read this phase
needs is a repository-level ``select``/join scoped by ``user_id`` on the row
itself (§4 item 3 — a card belongs to the learner, not reachable by joining
upward through its source lesson/path), and adding back-populated relationships
onto ``User``/``Lesson``/``Path`` would mean editing those modules, which is out
of this ticket's scope. The FK columns alone are what the schema needs.
"""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from datetime import (  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
    date,
    datetime,
)

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from aleph.db import Base
from aleph.models.base import UUIDAuditMixin
from aleph.models.enums import FlashcardDraftRunState, FlashcardGrade


class Flashcard(Base, UUIDAuditMixin):
    """A learner's card: front/back plus its scheduling projection and citation.

    ``kept_at IS NULL`` is a **Draft** (D6) — a row the drafting agent proposed
    but the learner has not kept yet. ``rung``/``due_on`` are ``NULL`` for a
    draft and are set atomically the moment it is kept
    (:meth:`~aleph.repositories.flashcards.FlashcardRepository.keep_drafts`,
    §5.2); thereafter they are the D1 projection, written only alongside a
    ``flashcard_reviews`` row.

    ``user_id`` is denormalized here rather than reached by joining through
    ``source_path_id``/``source_lesson_id`` (§4 item 3): a card outlives its
    source (D12) and belongs to the learner, not the lesson, so ownership has
    to live on the row itself or an orphaned card becomes unscopeable.

    Both source FKs are ``ON DELETE SET NULL`` and both titles are copied at
    draft time (§4 item 4, D12): deleting a path cascades to its lessons, so a
    single delete nulls both FKs, and the copied titles keep the card's own
    citation line renderable with no join. ``source_generated_at`` is the
    revision detector — the citation is a link iff the source lesson row still
    exists *and* its live ``generated_at`` still equals this stamp.
    """

    __tablename__ = "flashcards"
    __table_args__ = (
        # The hot path (§4 item 1): covers both the predicate and the ordering
        # the daily selection needs, and — partial, mirroring the Phase 5 D6
        # shape (``lessons ... WHERE completed_at IS NOT NULL``) — excludes
        # drafts, which the queue never wants and which would otherwise be most
        # of the index's size on an actively-drafting learner.
        Index(
            "ix_flashcards_user_id_due_on",
            "user_id",
            "due_on",
            postgresql_where=text("kept_at IS NOT NULL"),
        ),
        Index("ix_flashcards_source_lesson_id", "source_lesson_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    front: Mapped[str] = mapped_column(Text, nullable=False)
    back: Mapped[str] = mapped_column(Text, nullable=False)
    kept_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rung: Mapped[int | None] = mapped_column(Integer, nullable=True)
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_lesson_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("lessons.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_path_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("paths.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_lesson_title: Mapped[str] = mapped_column(Text, nullable=False)
    source_path_title: Mapped[str] = mapped_column(Text, nullable=False)
    source_generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class FlashcardReview(Base, UUIDAuditMixin):
    """One graded review — append-only, and the source of truth (D1).

    Never updated or deleted by anything in this design: a card's ``rung``/
    ``due_on`` is rebuildable in full by folding :func:`apply_grade` (owned by
    a concurrent ticket, :mod:`aleph.domains.scheduling`) over this table's rows
    for one card, ordered by ``reviewed_at`` — the replay property D1 promises.

    ``due_on_before`` is not audit decoration (§4 item 2): it is what lets the
    candidate query (§5.3) recover a card's *start-of-day* ``due_on`` after
    today's own grade has already moved the live projection into the future —
    the concrete payoff of making the log authoritative rather than derived.

    ``local_day`` is the learner's day **at write time** (Phase 5 D3/D4's day
    boundary) — a scheduling fact the "reviewed today" queue arm needs frozen,
    deliberately distinct from the **streak** fact
    (:meth:`~aleph.repositories.flashcards.FlashcardRepository.review_days_for_user`)
    recomputed from ``reviewed_at`` (§5.5, D11): storing one column for two
    purposes would make a learner who crosses a date line move both at once.
    """

    __tablename__ = "flashcard_reviews"
    __table_args__ = (
        Index("ix_flashcard_reviews_card_id_reviewed_at", "card_id", "reviewed_at"),
        Index("ix_flashcard_reviews_user_id_local_day", "user_id", "local_day"),
        Index("ix_flashcard_reviews_user_id_reviewed_at", "user_id", "reviewed_at"),
    )

    card_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("flashcards.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Denormalized off ``card_id`` for the same reason as ``Flashcard.user_id``
    # (§4 item 3): the streak union (§5.5) and the "another learner's reviews
    # never appear" invariant both need ownership on the row itself.
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    grade: Mapped[FlashcardGrade] = mapped_column(
        Enum(
            FlashcardGrade,
            name="flashcard_grade",
            values_callable=lambda enum: [e.value for e in enum],
        ),
        nullable=False,
    )
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    local_day: Mapped[date] = mapped_column(Date, nullable=False)
    rung_before: Mapped[int] = mapped_column(Integer, nullable=False)
    rung_after: Mapped[int] = mapped_column(Integer, nullable=False)
    due_on_before: Mapped[date] = mapped_column(Date, nullable=False)
    due_on_after: Mapped[date] = mapped_column(Date, nullable=False)


class FlashcardDraftRun(Base):
    """One sparse row per *drafted* lesson: drafting's claim/state row (D7).

    Deliberately **not** a :class:`~aleph.models.base.UUIDAuditMixin` table —
    ``lesson_id`` is the primary key (the ``UserFeatureOverride`` precedent,
    ``models/feature_flags.py``), which is what makes the claim a plain
    ``INSERT ... ON CONFLICT (lesson_id) DO UPDATE ... WHERE ...`` rather than a
    lookup-then-insert race. *Generating* has no card rows yet, so the state
    cannot live on the drafts themselves (D7's rationale over three nullable
    columns on ``lessons``).
    """

    __tablename__ = "flashcard_draft_runs"

    lesson_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("lessons.id", ondelete="CASCADE"),
        primary_key=True,
    )
    state: Mapped[FlashcardDraftRunState] = mapped_column(
        Enum(
            FlashcardDraftRunState,
            name="flashcard_draft_run_state",
            values_callable=lambda enum: [e.value for e in enum],
        ),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
