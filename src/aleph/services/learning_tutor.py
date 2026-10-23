"""Account-wide Tutor state use cases; no generation or target creation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aleph.domains.learning_recommendations import (
    RecommendationConflict,
    normalize_topic,
    transition,
)
from aleph.dtos.learning_tutor import (
    EvidenceReference,
    HistoryPage,
    LearningTutorStateDTO,
    OperationDTO,
    RecommendationDraft,
    RecommendationDTO,
    RecommendationPage,
)
from aleph.models.learning_tutor import LearningRecommendation as Card
from aleph.models.learning_tutor import LearningTutorState as State
from aleph.repositories.learning_tutor import LearningTutorRepository
from aleph.services.feature_flags import FeatureFlag, FeatureFlagService

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.domains.learning_recommendations import Decision
    from aleph.models import User
    from aleph.models.learning_tutor import LearningTutorOperation


class LearningTutorUnavailable(ValueError):  # noqa: N818 - domain outcome
    """Unavailable feature or unowned resource; concealed as 404."""


def operation_dto(row: LearningTutorOperation, now: datetime) -> OperationDTO:
    expired = row.status == "running" and row.expires_at <= now
    cleared = row.cleared_at is not None
    return OperationDTO.model_validate(
        dict(
            id=row.id,
            epoch=row.epoch,
            kind=row.kind,
            status="failed" if expired else row.status,
            error_code=None
            if cleared
            else ("interrupted" if expired else row.error_code),
            input=None if cleared else row.input,
            output=None if cleared else row.output,
            started_at=row.started_at,
            finished_at=row.expires_at if expired else row.finished_at,
            cleared=cleared,
        )
    )


def recommendation_dto(row: Card) -> RecommendationDTO:
    target_id = row.accepted_path_id or row.accepted_beat_id
    return RecommendationDTO.model_validate(
        dict(
            id=row.id,
            kind=row.kind,
            status=row.status,
            topic=row.topic,
            level=row.level,
            guidance=row.guidance,
            description=row.description,
            rationale=row.rationale,
            evidence=row.evidence,
            created_at=row.created_at,
            accepted_at=row.accepted_at,
            target_id=target_id,
            target_available=target_id is not None,
        )
    )


class LearningTutorService:
    def __init__(self, session: AsyncSession, user: User):
        self.session = session
        self.user = user
        self.repository = LearningTutorRepository(session)

    async def flags(self) -> dict[str, bool]:
        flags = await FeatureFlagService(self.session).resolve_for_user(self.user)
        if not flags.get(FeatureFlag.LEARNING_TUTOR):
            raise LearningTutorUnavailable()
        return flags

    async def state_or_empty(self) -> State:
        return await self.repository.state(self.user.id) or State(
            user_id=self.user.id,
            focus=None,
            revision=0,
            focus_version=0,
            epoch=0,
        )

    async def stale(self, row: Card, state: State) -> bool:
        if row.status == "accepted":
            return False
        if row.epoch != state.epoch or row.focus_version != state.focus_version:
            return True
        for raw in row.evidence or []:
            ref = EvidenceReference.model_validate(raw)
            if not await self.repository.source_exists(self.user.id, ref):
                return True
        return False

    async def supersede_stale(self, state: State) -> list[Card]:
        """Must hold state lock; free stale unique keys before inserting/restoring."""
        active = []
        for row in await self.repository.recommendations(self.user.id, "active"):
            if await self.stale(row, state):
                row.status = "superseded"
            else:
                active.append(row)
        await self.session.flush()
        return active

    async def history(self, before: UUID | None = None) -> HistoryPage:
        await self.flags()
        state = await self.state_or_empty()
        cursor = (
            await self.repository.operation(self.user.id, before) if before else None
        )
        if before and (
            cursor is None
            or cursor.epoch != state.epoch
            or cursor.cleared_at is not None
            or cursor.kind != "turn"
            or cursor.status != "succeeded"
        ):
            raise LearningTutorUnavailable()
        rows = await self.repository.history(self.user.id, state.epoch, cursor)
        now = await self.repository.now()
        return HistoryPage(
            items=[operation_dto(row, now) for row in rows[:20]],
            before=rows[19].id if len(rows) > 20 else None,
        )

    async def read(self) -> LearningTutorStateDTO:
        flags = await self.flags()
        state = await self.state_or_empty()
        active = []
        for row in await self.repository.recommendations(self.user.id, "active"):
            if (
                row.kind != "beat" or flags.get(FeatureFlag.ANALYST)
            ) and not await self.stale(row, state):
                active.append(recommendation_dto(row))
        now = await self.repository.now()
        running = await self.repository.running(self.user.id)
        refresh = (
            await self.repository.operation(
                self.user.id, state.last_refresh_operation_id
            )
            if state.last_refresh_operation_id
            else None
        )
        return LearningTutorStateDTO(
            focus=state.focus,
            revision=state.revision,
            focus_version=state.focus_version,
            epoch=state.epoch,
            recommendations=active,
            latest_refresh=operation_dto(refresh, now) if refresh else None,
            current_operation_id=running.id
            if running and running.expires_at > now and running.epoch == state.epoch
            else None,
            history=await self.history(),
        )

    async def operation(self, operation_id: UUID) -> OperationDTO:
        await self.flags()
        row = await self.repository.operation(self.user.id, operation_id)
        if row is None:
            raise LearningTutorUnavailable()
        return operation_dto(row, await self.repository.now())

    async def recommendation(self, recommendation_id: UUID) -> RecommendationDTO:
        flags = await self.flags()
        row = await self.repository.recommendation(self.user.id, recommendation_id)
        if row is None or (row.kind == "beat" and not flags.get(FeatureFlag.ANALYST)):
            raise LearningTutorUnavailable()
        if row.status == "superseded" or await self.stale(
            row, await self.state_or_empty()
        ):
            raise RecommendationConflict("recommendation_unavailable")
        return recommendation_dto(row)

    async def dismissed(self, before: UUID | None = None) -> RecommendationPage:
        flags = await self.flags()
        state = await self.state_or_empty()
        cursor = (
            await self.repository.recommendation(self.user.id, before)
            if before
            else None
        )
        if before and (cursor is None or cursor.epoch != state.epoch):
            raise LearningTutorUnavailable()
        rows = await self.repository.recommendation_page(self.user.id, cursor)
        visible = []
        for row in rows[:20]:
            if (
                row.kind != "beat" or flags.get(FeatureFlag.ANALYST)
            ) and not await self.stale(row, state):
                visible.append(recommendation_dto(row))
        return RecommendationPage(
            items=visible, before=rows[19].id if len(rows) > 20 else None
        )

    async def checked_state(self, revision: int) -> State:
        await self.flags()
        state = await self.repository.lock_state(self.user.id)
        if state.revision != revision:
            raise RecommendationConflict("stale_state")
        return state

    async def save_focus(
        self, focus: str | None, *, revision: int
    ) -> LearningTutorStateDTO:
        if focus is not None and len(focus) > 2000:
            raise ValueError("focus exceeds 2000 characters")
        state = await self.checked_state(revision)
        focus = (focus.strip() or None) if focus is not None else None
        if state.focus != focus:
            state.focus = focus
            state.focus_version += 1
            state.revision += 1
            await self.supersede_stale(state)
        await self.session.commit()
        return await self.read()

    async def clear(self, *, revision: int) -> LearningTutorStateDTO:
        state = await self.checked_state(revision)
        await self.repository.clear(self.user.id, await self.repository.now())
        state.epoch += 1
        state.revision += 1
        state.last_refresh_operation_id = None
        await self.session.commit()
        return await self.read()

    async def decide(
        self, recommendation_id: UUID, decision: Decision, *, revision: int
    ) -> LearningTutorStateDTO:
        flags = await self.flags()
        # Check ownership before revision to keep foreign identifiers concealed.
        row = await self.repository.recommendation(self.user.id, recommendation_id)
        if row is None or (row.kind == "beat" and not flags.get(FeatureFlag.ANALYST)):
            raise LearningTutorUnavailable()
        state = await self.checked_state(revision)
        row = await self.repository.recommendation(
            self.user.id, recommendation_id, lock=True
        )
        if row is None:
            raise LearningTutorUnavailable()
        active = await self.supersede_stale(state)
        next_status = transition(
            row.status,
            decision,
            stale=await self.stale(row, state),
            active_count=len(active),
        )
        if decision == "restore":
            keys = {
                normalize_topic(topic)
                for topic in await self.repository.target_topics(self.user.id, row.kind)
            }
            keys.update(
                card.normalized_topic
                for card in active
                if card.kind == row.kind and card.normalized_topic is not None
            )
            if row.normalized_topic in keys:
                raise RecommendationConflict("recommendation_unavailable")
        row.status = next_status
        row.dismissed_at = (
            await self.repository.now() if decision == "dismiss" else None
        )
        state.revision += 1
        await self.session.commit()
        return await self.read()

    async def add_recommendation(
        self,
        draft: RecommendationDraft,
        *,
        operation_id: UUID | None = None,
    ) -> Card:
        """Transaction-owned publication seam for LT-2; acquires the state lock.

        No commit/revision bump: a whole successful Turn/cards publishes together.
        Seed tests and later settlement use the same exclusion/cap/source rules.
        """
        flags = await self.flags()
        if draft.kind == "beat" and not flags.get(FeatureFlag.ANALYST):
            raise LearningTutorUnavailable()
        state = await self.repository.lock_state(self.user.id)
        active = await self.supersede_stale(state)
        if len(active) >= 3:
            raise RecommendationConflict("recommendation_capacity")
        key = normalize_topic(draft.topic)
        excluded = {
            normalize_topic(topic)
            for topic in await self.repository.target_topics(self.user.id, draft.kind)
        }
        if key in excluded or await self.repository.topic_excluded(
            self.user.id, draft.kind, key
        ):
            raise RecommendationConflict("recommendation_unavailable")
        for ref in draft.evidence:
            if not await self.repository.source_exists(self.user.id, ref):
                raise RecommendationConflict("recommendation_unavailable")
        row = Card(
            user_id=self.user.id,
            epoch=state.epoch,
            focus_version=state.focus_version,
            operation_id=operation_id,
            normalized_topic=key,
            **draft.model_dump(mode="json"),
        )
        self.session.add(row)
        await self.session.flush()
        return row
