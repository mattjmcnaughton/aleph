"""Data access for attempts (a learner answering a Quick check; first wins)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aleph.models import Attempt

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession


class AttemptRepository:
    """Data access for :class:`~aleph.models.Attempt` rows.

    One Attempt per learner per Quick check — the first answer is the Outcome of
    record (activation counts it, §4). :meth:`record` enforces first-wins
    atomically via ``INSERT ... ON CONFLICT DO NOTHING``, so a double submit
    never overwrites the recorded answer.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        quick_check_id: uuid.UUID,
        user_id: uuid.UUID,
        selected_index: int,
        is_correct: bool,
    ) -> tuple[Attempt, bool]:
        """Record an Attempt, returning ``(attempt, created)``.

        ``created`` is ``False`` when an Attempt already existed — the returned
        row is then the pre-existing first answer, unchanged.
        """
        insert = (
            pg_insert(Attempt)
            .values(
                quick_check_id=quick_check_id,
                user_id=user_id,
                selected_index=selected_index,
                is_correct=is_correct,
            )
            .on_conflict_do_nothing(
                index_elements=[Attempt.quick_check_id, Attempt.user_id]
            )
            .returning(Attempt.id)
        )
        inserted_id = (await self.session.execute(insert)).scalar_one_or_none()
        created = inserted_id is not None

        existing = await self.get(quick_check_id=quick_check_id, user_id=user_id)
        if existing is None:  # pragma: no cover - the row is guaranteed to exist
            msg = "attempt row vanished after upsert"
            raise RuntimeError(msg)
        return existing, created

    async def get(
        self, *, quick_check_id: uuid.UUID, user_id: uuid.UUID
    ) -> Attempt | None:
        result = await self.session.execute(
            select(Attempt).where(
                Attempt.quick_check_id == quick_check_id,
                Attempt.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()
