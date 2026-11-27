"""Data access for Reasonings (a learner's own-words "why" for an Attempt)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update

from aleph.models import Reasoning

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession


class ReasoningRepository:
    """Data access for :class:`~aleph.models.Reasoning` rows (1:1 with an Attempt).

    Every write after the insert is a guarded ``UPDATE`` so concurrent requests
    cannot overwrite each other: the first grading of a round wins, and only
    one follow-up answer is ever accepted.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, *, attempt_id: uuid.UUID, text: str) -> Reasoning:
        reasoning = Reasoning(attempt_id=attempt_id, text=text)
        self.session.add(reasoning)
        await self.session.flush()
        return reasoning

    async def get_for_attempt(self, attempt_id: uuid.UUID) -> Reasoning | None:
        result = await self.session.execute(
            select(Reasoning).where(Reasoning.attempt_id == attempt_id)
        )
        return result.scalar_one_or_none()

    async def record_assessment(
        self, reasoning_id: uuid.UUID, assessment: dict[str, Any] | None
    ) -> None:
        """Store the first-round grading, or mark it failed when ``None``."""
        values: dict[str, Any] = (
            {"assessment": assessment}
            if assessment is not None
            else {"grading_failed": True}
        )
        await self.session.execute(
            update(Reasoning)
            .where(
                Reasoning.id == reasoning_id,
                Reasoning.assessment.is_(None),
                Reasoning.grading_failed.is_(False),
            )
            .values(**values)
        )

    async def claim_follow_up(self, reasoning_id: uuid.UUID, answer: str) -> bool:
        """Store the learner's follow-up answer; ``False`` if one already exists."""
        result = await self.session.execute(
            update(Reasoning)
            .where(
                Reasoning.id == reasoning_id,
                Reasoning.follow_up_answer.is_(None),
            )
            .values(follow_up_answer=answer)
            .returning(Reasoning.id)
        )
        return result.scalar_one_or_none() is not None

    async def record_follow_up_assessment(
        self, reasoning_id: uuid.UUID, assessment: dict[str, Any] | None
    ) -> None:
        """Store the follow-up grading, or mark it failed when ``None``."""
        values: dict[str, Any] = (
            {"follow_up_assessment": assessment}
            if assessment is not None
            else {"follow_up_grading_failed": True}
        )
        await self.session.execute(
            update(Reasoning)
            .where(
                Reasoning.id == reasoning_id,
                Reasoning.follow_up_assessment.is_(None),
                Reasoning.follow_up_grading_failed.is_(False),
            )
            .values(**values)
        )
