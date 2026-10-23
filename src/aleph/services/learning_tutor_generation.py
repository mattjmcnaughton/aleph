"""Durable admission and fenced publication; providers never hold a DB connection."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, timedelta
from typing import TYPE_CHECKING

from pydantic import ValidationError
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.usage import RunUsage

from aleph.agents.learning_tutor import build_learning_tutor_agent, output_violation
from aleph.authz import is_admin
from aleph.config import settings
from aleph.db import new_session
from aleph.domains.learning_recommendations import RecommendationConflict
from aleph.dtos.learning_tutor import RecommendationDraft
from aleph.models import User
from aleph.models.learning_tutor import LearningTutorOperation as Operation
from aleph.services.feature_flags import FeatureFlag
from aleph.services.learning_tutor import (
    LearningTutorService,
    LearningTutorUnavailable,
    emit_operation_completed,
    operation_dto,
)
from aleph.services.learning_tutor_context import assemble_context
from aleph.services.openrouter import resolve_model

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine
    from contextlib import AbstractAsyncContextManager
    from typing import Any
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.agents.learning_tutor import LearningTutorDeps, LearningTutorOutput
    from aleph.config import Settings
    from aleph.dtos.learning_tutor import OperationDTO, OperationRequest
    from aleph.services.learning_tutor_context import LearningContext


class LearningTutorQuotaExceeded(ValueError):  # noqa: N818 - domain outcome
    """Admitted receipt cap reached; clearing does not refund it."""


class LearningTutorBusy(RecommendationConflict):
    def __init__(self, operation_id: UUID):
        super().__init__("operation_in_progress")
        self.operation_id = operation_id


class InvalidLearningOutput(ValueError):  # noqa: N818 - domain outcome
    """Invalid whole output, never a successful empty result."""

    def __init__(self, input_tokens: int = 0, output_tokens: int = 0):
        super().__init__("invalid_output")
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class LearningTutorGeneration:
    def __init__(
        self,
        *,
        config: Settings = settings,
        session_factory: Callable[
            [], AbstractAsyncContextManager[AsyncSession]
        ] = new_session,
        provider: Callable[
            [LearningTutorDeps], Awaitable[tuple[LearningTutorOutput, int, int]]
        ]
        | None = None,
        spawn: Callable[[Coroutine[Any, Any, None]], Any] | None = None,
    ):
        self.config = config
        self.session_factory = session_factory
        self.provider = provider or self._provide
        self.spawn = spawn
        self.semaphore = asyncio.Semaphore(
            config.max_concurrent_learning_tutor_operations
        )

    async def _provide(
        self, deps: LearningTutorDeps
    ) -> tuple[LearningTutorOutput, int, int]:
        usage = RunUsage()
        try:
            result = await build_learning_tutor_agent().run(
                "Respond to the current request in the supplied context.",
                deps=deps,
                model=resolve_model(self.config.model_learning_tutor),
                usage=usage,
            )
        except (UnexpectedModelBehavior, ValidationError):
            raise InvalidLearningOutput(
                usage.input_tokens, usage.output_tokens
            ) from None
        return result.output, usage.input_tokens, usage.output_tokens

    async def admit(
        self, service: LearningTutorService, request: OperationRequest
    ) -> OperationDTO:
        session, repo = service.session, service.repository
        flags = await service.flags()
        state = await repo.lock_state(service.user.id)
        if request.epoch != state.epoch:
            raise RecommendationConflict("stale_state")
        body = request.model_dump(mode="json")
        fingerprint = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        now = await repo.now()
        replay = await repo.replay(service.user.id, state.epoch, request.request_id)
        if replay:
            if replay.request_hash != fingerprint:
                raise RecommendationConflict("request_reused")
            result = operation_dto(replay, now)
            await session.commit()
            return result
        selected = None
        if request.recommendation_id:
            selected = await repo.recommendation(
                service.user.id, request.recommendation_id
            )
            if selected is None or (
                selected.kind == "beat" and not flags.get(FeatureFlag.ANALYST)
            ):
                raise LearningTutorUnavailable()
        if state.revision != request.revision:
            raise RecommendationConflict("stale_state")
        running = await repo.running(service.user.id)
        if running:
            if running.expires_at > now:
                raise LearningTutorBusy(running.id)
            running.status, running.error_code, running.finished_at = (
                "failed",
                "interrupted",
                now,
            )
            await session.flush()
        active = await service.supersede_stale(state)
        if selected is not None and selected not in active:
            raise RecommendationConflict("recommendation_unavailable")
        context = await assemble_context(
            session,
            service.user.id,
            state,
            request,
            analyst_enabled=bool(flags.get(FeatureFlag.ANALYST)),
            active=active,
            selected=selected,
        )
        cold = request.kind == "refresh" and context.needs_context
        cap = self.config.rate_limit_learning_tutor_per_day
        if not cold and cap and not is_admin(service.user, self.config):
            midnight = now.astimezone(UTC).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            if await repo.admissions_since(service.user.id, midnight) >= cap:
                raise LearningTutorQuotaExceeded()
        await repo.prune_cleared(service.user.id, now - timedelta(days=30))
        row = Operation(
            user_id=service.user.id,
            epoch=state.epoch,
            request_id=request.request_id,
            request_hash=fingerprint,
            kind=request.kind,
            revision=state.revision,
            status="succeeded" if cold else "running",
            started_at=now,
            expires_at=now + timedelta(seconds=120),
            model_admitted=not cold,
            input=body,
            output={"outcome": "needs_context", "omitted": context.omitted}
            if cold
            else None,
            finished_at=now if cold else None,
        )
        session.add(row)
        await session.flush()
        if request.kind == "refresh":
            state.last_refresh_operation_id = row.id
        if cold:
            state.revision += 1
        result = operation_dto(row, now)
        user_id, operation_id = service.user.id, row.id
        await session.commit()
        if running:
            emit_operation_completed(running)
        if cold:
            emit_operation_completed(row)
        if not cold:
            work = self.execute(user_id, operation_id, context)
            try:
                if self.spawn is None:
                    raise RuntimeError("runtime not started")
                self.spawn(work)
            except Exception:
                work.close()
                await self.fail(user_id, operation_id, "interrupted")
                return await service.operation(operation_id)
        return result

    async def execute(
        self, user_id: UUID, operation_id: UUID, context: LearningContext
    ) -> None:
        input_tokens = output_tokens = 0
        try:
            async with asyncio.timeout(self.config.learning_tutor_timeout):
                async with self.semaphore:
                    output, input_tokens, output_tokens = await self.provider(
                        context.deps
                    )
                    if output_violation(output, context.deps):
                        raise InvalidLearningOutput()
                await self.settle(
                    user_id, operation_id, context, output, input_tokens, output_tokens
                )
        except asyncio.CancelledError:
            # Shutdown/process loss keeps the durable lease; GET exposes expiry.
            raise
        except TimeoutError:
            await self.fail(
                user_id, operation_id, "timed_out", input_tokens, output_tokens
            )
        except (InvalidLearningOutput, UnexpectedModelBehavior, ValidationError) as exc:
            if isinstance(exc, InvalidLearningOutput):
                input_tokens = max(input_tokens, exc.input_tokens)
                output_tokens = max(output_tokens, exc.output_tokens)
            await self.fail(
                user_id, operation_id, "invalid_output", input_tokens, output_tokens
            )
        except Exception:
            # Never log provider exceptions: they can contain prompt/reply text.
            await self.fail(
                user_id, operation_id, "provider_failed", input_tokens, output_tokens
            )

    async def fail(
        self,
        user_id: UUID,
        operation_id: UUID,
        reason: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        async with self.session_factory() as session:
            user = await session.get(User, user_id)
            if user is None:
                return
            service = LearningTutorService(session, user)
            await service.repository.lock_state(user_id)
            row = await service.repository.operation(user_id, operation_id, lock=True)
            if row is None:
                return
            row.input_tokens = max(row.input_tokens, input_tokens)
            row.output_tokens = max(row.output_tokens, output_tokens)
            running = row.status == "running"
            if running:
                now = await service.repository.now()
                row.status, row.finished_at = "failed", now
                row.error_code = "interrupted" if row.expires_at <= now else reason
            await session.commit()
            if running:
                emit_operation_completed(row)

    async def settle(
        self,
        user_id: UUID,
        operation_id: UUID,
        context: LearningContext,
        output: LearningTutorOutput,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        async with self.session_factory() as session:
            user = await session.get(User, user_id)
            if user is None:
                return
            service = LearningTutorService(session, user)
            repo = service.repository
            state = await repo.lock_state(user_id)
            row = await repo.operation(user_id, operation_id, lock=True)
            if row is None:
                return
            # Late usage enriches receipts, never resurrects terminal content.
            row.input_tokens = max(row.input_tokens, input_tokens)
            row.output_tokens = max(row.output_tokens, output_tokens)
            if row.cleared_at is not None or row.status != "running":
                await session.commit()
                return
            now = await repo.now()
            reason = None
            if row.expires_at <= now:
                reason = "interrupted"
            elif row.epoch != state.epoch or row.revision != state.revision:
                reason = "context_changed"
            try:
                flags = await service.flags()
                if context.deps.analyst_enabled and not flags.get(FeatureFlag.ANALYST):
                    reason = "feature_disabled"
            except LearningTutorUnavailable:
                reason = "feature_disabled"
            # Prose can also rely on supplied facts, not only card references.
            refs = list(context.evidence.values())
            if not all([await repo.source_exists(user_id, ref) for ref in refs]):
                reason = "context_changed"
            if reason:
                row.status, row.error_code, row.finished_at = "failed", reason, now
                await session.commit()
                emit_operation_completed(row)
                return
            # Savepoint ensures an invalid later card cannot leave earlier cards or
            # supersession behind. Only the failed receipt commits on rejection.
            try:
                async with session.begin_nested():
                    active = await service.supersede_stale(state)
                    for card in active:
                        if row.kind == "refresh" or card.id == output.replacement:
                            card.status = "superseded"
                    await session.flush()
                    card_ids = []
                    for draft in output.recommendations:
                        card = await service.add_recommendation(
                            RecommendationDraft(
                                **draft.model_dump(exclude={"evidence_keys"}),
                                evidence=[
                                    context.evidence[key] for key in draft.evidence_keys
                                ],
                            ),
                            operation_id=row.id,
                        )
                        card_ids.append(str(card.id))
                    row.output = dict(
                        output.model_dump(mode="json"),
                        recommendation_ids=card_ids,
                        omitted=context.omitted,
                        outcome="completed",
                    )
                    row.status, row.finished_at = "succeeded", now
                    state.revision += 1
            except (RecommendationConflict, LearningTutorUnavailable):
                row.status, row.error_code, row.finished_at = (
                    "failed",
                    "context_changed",
                    now,
                )
            await session.commit()
            emit_operation_completed(row)


learning_tutor_generation = LearningTutorGeneration()
