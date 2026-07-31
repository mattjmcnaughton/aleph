"""Message ORM model (CONTEXT.md: a single utterance in a conversation)."""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from typing import TYPE_CHECKING, Any

from sqlalchemy import Enum, ForeignKey, Index, Integer, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aleph.db import Base
from aleph.models.base import UUIDAuditMixin
from aleph.models.enums import MessageRole, MessageSource

if TYPE_CHECKING:
    from aleph.models.conversation import Conversation


class Message(Base, UUIDAuditMixin):
    """One utterance in a conversation — learner or tutor.

    ``position`` is the thread's total order, assigned at persist time
    (``max + 1`` / ``max + 2`` for a turn's pair, Phase 2 TDD §4). The
    per-conversation in-flight lock (D9) makes collisions unreachable in
    practice; ``uq_messages_conversation_position`` makes them loud, not silent,
    if that lock is ever bypassed.

    ``lesson_id`` records **the lesson the message was asked in**, which is what
    lets a revisited thread show where each turn happened.

    **Column applicability is by role and app-enforced — there are deliberately
    no CHECK constraints** (TDD §4): ``source`` belongs on learner rows,
    ``tutor_check`` and ``proposal`` on tutor rows (``proposal`` additionally
    only in a ``shaping`` conversation, Phase 2B TDD §4). ``tutor_check`` holds
    ``{stem, options, correct_index, explanation, answered_index}``; a Tutor
    check is non-scoring and outside progress — it creates no
    :class:`~aleph.models.Attempt` and touches no Phase 1 table, which is a
    property of this schema rather than a convention. ``answered_index`` is
    written by the check-answer endpoint (§6) so a revisit renders the revealed
    state.
    """

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "position",
            name="uq_messages_conversation_position",
        ),
        # The FK cascade from ``lessons`` scans this column; the (conversation_id,
        # position) unique index already covers reads and cascades on the
        # conversation side.
        Index("ix_messages_lesson_id", "lesson_id"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[MessageRole] = mapped_column(
        Enum(
            MessageRole,
            name="message_role",
            values_callable=lambda enum: [e.value for e in enum],
        ),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    lesson_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("lessons.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[MessageSource | None] = mapped_column(
        Enum(
            MessageSource,
            name="message_source",
            values_callable=lambda enum: [e.value for e in enum],
        ),
        nullable=True,
    )
    # Reassign, never mutate in place: plain JSONB has no ORM mutation tracking,
    # so an in-place dict edit is invisible to the session and never flushed.
    tutor_check: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # The tutor's **Proposal** — its validated edit plan (CONTEXT.md) — carried
    # exactly as ``tutor_check`` is, and under the same reassign-never-mutate
    # rule. ``{operations: [...], summary}`` (Phase 2B TDD §4): data, not prose;
    # the reply text explains, this payload is what applies.
    #
    # **The Proposal's resolution state is derived, never stored here** (D3): it
    # is *applied* if a live ``path_changes`` row references this message,
    # *undone* if that row is undone, *superseded* if a later proposal was
    # applied first and re-validation now fails, else *pending*. No status
    # column means no status to keep consistent.
    proposal: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
