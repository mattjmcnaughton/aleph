"""Reasoning ORM model (CONTEXT.md: a learner's own-words "why" for an Attempt)."""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Text, UniqueConstraint, Uuid, false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aleph.db import Base
from aleph.models.base import UUIDAuditMixin

if TYPE_CHECKING:
    from aleph.models.attempt import Attempt


class Reasoning(Base, UUIDAuditMixin):
    """The "why" a learner wrote with their first Attempt, and its grading.

    One per Attempt (unique ``attempt_id``), written in the same transaction as
    the Attempt, so first-wins carries over: a resubmitted Attempt never adds or
    replaces one. ``assessment`` is the grader's report (``agents/
    reasoning_grader.ReasoningAssessment``), null until graded; the grade itself
    is re-derived from it on read (``domains/grading.grade_reasoning``).
    ``grading_failed`` records a grader that errored or timed out, distinct from
    one still running. The follow-up columns hold the learner's one answer to
    the grader's follow-up question and its grading, on the same pattern.
    Formative only: nothing here touches progress or metrics.
    """

    __tablename__ = "reasonings"
    __table_args__ = (UniqueConstraint("attempt_id", name="uq_reasonings_attempt"),)

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("attempts.id", ondelete="CASCADE"),
        nullable=False,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    assessment: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    grading_failed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    follow_up_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    follow_up_assessment: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    follow_up_grading_failed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    attempt: Mapped[Attempt] = relationship(back_populates="reasoning")
