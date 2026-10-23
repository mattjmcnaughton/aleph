"""Owned state queries and transaction-scoped locking/receipt seams.

Never commits. Callers serialize writes with lock_state; future acceptance must
first lock users, then state, then recommendation. Reads never lazy-create rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert

from aleph.models import Beat, Brief, Path
from aleph.models.learning_tutor import LearningRecommendation as Card
from aleph.models.learning_tutor import LearningTutorOperation as Operation
from aleph.models.learning_tutor import LearningTutorState as State

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.dtos.learning_tutor import EvidenceReference


class LearningTutorRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def now(self) -> datetime:
        return (await self.session.execute(select(func.clock_timestamp()))).scalar_one()

    async def state(self, user_id: UUID) -> State | None:
        return await self.session.get(State, user_id)

    async def lock_state(self, user_id: UUID) -> State:
        await self.session.execute(
            insert(State).values(user_id=user_id).on_conflict_do_nothing()
        )
        return (
            await self.session.execute(
                select(State)
                .where(State.user_id == user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()

    async def recommendation(
        self, user_id: UUID, recommendation_id: UUID, *, lock=False
    ) -> Card | None:
        query = select(Card).where(
            Card.user_id == user_id, Card.id == recommendation_id
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return await self.session.scalar(query)

    async def recommendations(self, user_id: UUID, status: str) -> list[Card]:
        return list(
            await self.session.scalars(
                select(Card)
                .where(Card.user_id == user_id, Card.status == status)
                .order_by(Card.created_at.desc(), Card.id.desc())
            )
        )

    async def recommendation_page(
        self, user_id: UUID, before: Card | None
    ) -> list[Card]:
        query = select(Card).where(Card.user_id == user_id, Card.status == "dismissed")
        if before:
            query = query.where(
                tuple_(Card.created_at, Card.id) < (before.created_at, before.id)
            )
        return list(
            await self.session.scalars(
                query.order_by(Card.created_at.desc(), Card.id.desc()).limit(21)
            )
        )

    async def operation(
        self, user_id: UUID, operation_id: UUID, *, lock=False
    ) -> Operation | None:
        query = select(Operation).where(
            Operation.user_id == user_id, Operation.id == operation_id
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return await self.session.scalar(query)

    async def replay(
        self, user_id: UUID, epoch: int, request_id: UUID
    ) -> Operation | None:
        """LT-2 checks epoch before replay, then hash before captured revision."""
        return await self.session.scalar(
            select(Operation).where(
                Operation.user_id == user_id,
                Operation.epoch == epoch,
                Operation.request_id == request_id,
            )
        )

    async def running(self, user_id: UUID) -> Operation | None:
        return await self.session.scalar(
            select(Operation).where(
                Operation.user_id == user_id, Operation.status == "running"
            )
        )

    async def history(
        self, user_id: UUID, epoch: int, before: Operation | None
    ) -> list[Operation]:
        query = select(Operation).where(
            Operation.user_id == user_id,
            Operation.epoch == epoch,
            Operation.kind == "turn",
            Operation.status == "succeeded",
            Operation.cleared_at.is_(None),
        )
        if before:
            query = query.where(
                tuple_(Operation.created_at, Operation.id)
                < (before.created_at, before.id)
            )
        return list(
            await self.session.scalars(
                query.order_by(Operation.created_at.desc(), Operation.id.desc()).limit(
                    21
                )
            )
        )

    async def source_exists(self, user_id: UUID, reference: EvidenceReference) -> bool:
        if reference.kind in {"focus", "message"}:
            return True
        if reference.kind == "brief":
            query = (
                select(Brief.id)
                .join(Beat, Brief.beat_id == Beat.id)
                .where(Beat.user_id == user_id, Brief.id == reference.id)
            )
        else:
            model = Path if reference.kind == "path" else Beat
            query = select(model.id).where(
                model.user_id == user_id, model.id == reference.id
            )
        return await self.session.scalar(query) is not None

    async def topic_excluded(self, user_id: UUID, kind: str, key: str) -> bool:
        """Check all retained dismissals in SQL, not just a context/page window."""
        return (
            await self.session.scalar(
                select(Card.id)
                .where(
                    Card.user_id == user_id,
                    Card.kind == kind,
                    Card.normalized_topic == key,
                    Card.status.in_(["active", "dismissed"]),
                )
                .limit(1)
            )
            is not None
        )

    async def target_topics(self, user_id: UUID, kind: str) -> list[str]:
        model = Path if kind == "path" else Beat
        return list(
            await self.session.scalars(
                select(model.topic).where(model.user_id == user_id)
            )
        )

    async def clear(self, user_id: UUID, now: datetime) -> None:
        await self.session.execute(
            update(Operation)
            .where(Operation.user_id == user_id, Operation.status == "running")
            .values(status="failed", finished_at=now)
        )
        await self.session.execute(
            update(Operation)
            .where(Operation.user_id == user_id, Operation.cleared_at.is_(None))
            .values(input=None, output=None, error_code=None, cleared_at=now)
        )
        await self.session.execute(
            delete(Card).where(Card.user_id == user_id, Card.status != "accepted")
        )
        await self.session.execute(
            update(Card)
            .where(Card.user_id == user_id, Card.status == "accepted")
            .values(
                topic=None,
                level=None,
                guidance=None,
                description=None,
                rationale=None,
                evidence=None,
                normalized_topic=None,
            )
        )
