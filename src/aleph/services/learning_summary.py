"""The Learning summary use case (CONTEXT.md: *Learning summary*).

Trigger + poll, like path generation: ``POST`` claims the account's one summary
row and spawns the run; ``GET`` reads it. The counted facts on the page come
from progress rows on every read and are never model-written. The model writes
only the areas and the suggestions, stored with the date they were written.

States the client can tell apart (Constitution §4):

* ``too_early``  — fewer than :data:`MIN_LESSONS_COMPLETE` lessons complete and
  nothing written yet; not an error.
* ``none``       — enough progress, nothing written yet.
* ``generating`` — a run is in flight (any earlier summary is still shown).
* ``ready``      — the last run succeeded.
* ``failed``     — the last run failed, or stalled past the stale timeout; the
  earlier summary, if any, is still shown and a retry is offered.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

import structlog

from aleph.agents.learning_summary import (
    BeatDigest,
    LearningSummaryBody,
    LearningSummaryDeps,
    PathDigest,
    build_learning_summary_agent,
    build_learning_summary_prompt,
)
from aleph.config import Settings
from aleph.config import settings as global_settings
from aleph.db import new_session
from aleph.repositories import UserRepository
from aleph.repositories.learning_summaries import (
    DigestBeat,
    DigestLesson,
    DigestPath,
    LearningSummaryRepository,
    SummaryFacts,
)
from aleph.services.openrouter import resolve_model

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable, Coroutine, Sequence
    from contextlib import AbstractAsyncContextManager

    from pydantic_ai.models import Model
    from sqlalchemy.ext.asyncio import AsyncSession

    SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
    Spawn = Callable[[Coroutine[Any, Any, Any]], Any]
    ModelSlot = Callable[[], AbstractAsyncContextManager[Any]]

logger = structlog.get_logger(__name__)

SummaryState = Literal["too_early", "none", "generating", "ready", "failed"]

# Below this, there is too little to summarise or build on.
MIN_LESSONS_COMPLETE = 3
# A refresh within this window of the last write is refused: each run is billed.
REFRESH_COOLDOWN = datetime.timedelta(minutes=2)
# How much of each path the model sees: recent completions and what is next.
_COMPLETED_TITLES = 12
_NEXT_TITLES = 3


class NotEnoughProgressError(Exception):
    """The account has fewer than :data:`MIN_LESSONS_COMPLETE` lessons complete."""


class RefreshTooSoonError(Exception):
    """A summary was written less than :data:`REFRESH_COOLDOWN` ago."""


@dataclass(frozen=True)
class SummaryView:
    """What ``GET`` (and ``POST``) answer with."""

    state: SummaryState
    facts: SummaryFacts
    body: dict[str, Any] | None
    written_at: datetime.datetime | None


def build_summary_deps(
    paths: Sequence[DigestPath],
    lessons: Sequence[DigestLesson],
    beats: Sequence[DigestBeat],
    about_you: str | None,
) -> LearningSummaryDeps:
    """Fold the digest rows into the agent's deps (pure)."""
    by_path: dict[uuid.UUID, list[DigestLesson]] = {}
    for lesson in lessons:
        by_path.setdefault(lesson.path_id, []).append(lesson)
    digests = []
    for path in paths:
        rows = by_path.get(path.id, [])
        done = [row.title for row in rows if row.complete]
        if not rows or not done:
            progress = "not_started"
        elif len(done) == len(rows):
            progress = "finished"
        else:
            progress = "in_progress"
        digests.append(
            PathDigest(
                topic=path.topic,
                level=path.level.value,
                progress=progress,
                lessons_complete=len(done),
                lessons_total=len(rows),
                completed_titles=tuple(done[-_COMPLETED_TITLES:]),
                next_titles=tuple(row.title for row in rows if not row.complete)[
                    :_NEXT_TITLES
                ],
                missed_titles=tuple(row.title for row in rows if row.missed),
            )
        )
    return LearningSummaryDeps(
        paths=tuple(digests),
        beats=tuple(BeatDigest(topic=b.topic, level=b.level.value) for b in beats),
        about_you=about_you,
    )


class LearningSummaryService:
    """Reads, triggers and runs the account's Learning summary."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory = new_session,
        spawn: Spawn = asyncio.create_task,
        resolve_model_fn: Callable[[str], Model] = resolve_model,
        config: Settings = global_settings,
        model_slot: ModelSlot = contextlib.nullcontext,
    ) -> None:
        self._session_factory = session_factory
        self._spawn = spawn
        self._base_spawn = spawn
        self._resolve_model = resolve_model_fn
        self._config = config
        self._model_slot = model_slot
        self._base_model_slot = model_slot

    def bind_runtime(self, *, spawn: Spawn, model_slot: ModelSlot) -> None:
        """Rebind the spawn and model-slot seams for the app's lifetime."""
        self._spawn = spawn
        self._model_slot = model_slot

    def reset_runtime(self) -> None:
        self._spawn = self._base_spawn
        self._model_slot = self._base_model_slot

    def _stale_before(self, now: datetime.datetime) -> datetime.datetime:
        return now - datetime.timedelta(
            seconds=self._config.generation_stale_after_seconds
        )

    async def read(self, session: AsyncSession, user_id: uuid.UUID) -> SummaryView:
        repo = LearningSummaryRepository(session)
        facts = await repo.facts(user_id)
        row = await repo.get(user_id)
        if row is None:
            state: SummaryState = (
                "too_early" if facts.lessons_complete < MIN_LESSONS_COMPLETE else "none"
            )
            return SummaryView(state=state, facts=facts, body=None, written_at=None)
        state = cast("SummaryState", row.status)
        now = datetime.datetime.now(datetime.UTC)
        if state == "generating" and row.started_at < self._stale_before(now):
            state = "failed"
        return SummaryView(
            state=state, facts=facts, body=row.body, written_at=row.written_at
        )

    async def trigger(self, session: AsyncSession, user_id: uuid.UUID) -> SummaryView:
        """Start a run unless one is in flight; answer with the current view.

        Raises :class:`NotEnoughProgressError` or :class:`RefreshTooSoonError`.
        A trigger while a run is in flight is not an error: it changes nothing
        and the view says ``generating``.
        """
        repo = LearningSummaryRepository(session)
        facts = await repo.facts(user_id)
        if facts.lessons_complete < MIN_LESSONS_COMPLETE:
            raise NotEnoughProgressError
        now = datetime.datetime.now(datetime.UTC)
        existing = await repo.get(user_id)
        if (
            existing is not None
            and existing.status == "ready"
            and existing.written_at is not None
            and now - existing.written_at < REFRESH_COOLDOWN
        ):
            raise RefreshTooSoonError
        fence = await repo.claim(user_id, stale_before=self._stale_before(now))
        await session.commit()
        if fence is not None:
            self._spawn(self.run(user_id, fence))
        return await self.read(session, user_id)

    async def run(self, user_id: uuid.UUID, fence: datetime.datetime) -> None:
        """Write the summary for a won claim and settle the row (fenced)."""
        async with self._model_slot():
            body: LearningSummaryBody | None = None
            try:
                async with self._session_factory() as session:
                    repo = LearningSummaryRepository(session)
                    deps = build_summary_deps(
                        await repo.digest_paths(user_id),
                        await repo.digest_lessons(user_id),
                        await repo.digest_beats(user_id),
                        await UserRepository(session).about_you(user_id),
                    )
                agent = build_learning_summary_agent()
                async with asyncio.timeout(self._config.generation_timeout_seconds):
                    result = await agent.run(
                        build_learning_summary_prompt(deps),
                        deps=deps,
                        model=self._resolve_model(self._config.model_default),
                    )
                body = result.output
            except Exception:  # noqa: BLE001 - any provider error is a failed run
                logger.exception("learning_summary_failed", user_id=str(user_id))
        async with self._session_factory() as session:
            repo = LearningSummaryRepository(session)
            if body is None:
                await repo.mark_failed(user_id, fence=fence)
            else:
                await repo.mark_ready(user_id, fence=fence, body=body.model_dump())
            await session.commit()


learning_summary_service = LearningSummaryService()
