"""Data access for the Learning summary (CONTEXT.md: *Learning summary*).

Two concerns: the one ``learning_summaries`` row per account (claim, settle,
read), and the read-only digest of the account's paths and Beats the summary
is written from. The repository never commits; the service owns the unit of
work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aleph.models import (
    Attempt,
    Beat,
    LearningSummary,
    Lesson,
    Path,
    PathStatus,
    QuickCheck,
)

if TYPE_CHECKING:
    import datetime
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.models import Level


@dataclass(frozen=True)
class DigestLesson:
    """One lesson of a ready path, in path order."""

    path_id: uuid.UUID
    title: str
    complete: bool
    missed: bool


@dataclass(frozen=True)
class DigestPath:
    """One ready path's identity, in creation order."""

    id: uuid.UUID
    topic: str
    level: Level


@dataclass(frozen=True)
class DigestBeat:
    """One Beat's topic and level."""

    topic: str
    level: Level


@dataclass(frozen=True)
class SummaryFacts:
    """The counted facts the summary page shows exactly (never model-written)."""

    lessons_complete: int
    paths: int
    beats: int


class LearningSummaryRepository:
    """The summary row plus the digest it is written from."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: uuid.UUID) -> LearningSummary | None:
        return await self.session.get(LearningSummary, user_id, populate_existing=True)

    async def claim(
        self, user_id: uuid.UUID, *, stale_before: datetime.datetime
    ) -> datetime.datetime | None:
        """Mark a run ``generating`` and return its fence, or ``None`` if busy.

        One statement, so two concurrent triggers cannot both win: the upsert
        only overwrites a row that is not already generating, or whose run is
        older than ``stale_before`` (a crashed or restarted process).
        """
        now = func.clock_timestamp()
        stmt = (
            pg_insert(LearningSummary)
            .values(user_id=user_id, status="generating", started_at=now)
            .on_conflict_do_update(
                index_elements=[LearningSummary.user_id],
                set_={"status": "generating", "started_at": now},
                where=(LearningSummary.status != "generating")
                | (LearningSummary.started_at < stale_before),
            )
            .returning(LearningSummary.started_at)
        )
        return await self.session.scalar(stmt)

    async def mark_ready(
        self, user_id: uuid.UUID, *, fence: datetime.datetime, body: dict[str, Any]
    ) -> bool:
        """Store a finished run's body, if this run still owns the row."""
        result = await self.session.execute(
            update(LearningSummary)
            .where(
                LearningSummary.user_id == user_id,
                LearningSummary.started_at == fence,
                LearningSummary.status == "generating",
            )
            .values(status="ready", body=body, written_at=func.now())
            .returning(LearningSummary.user_id)
        )
        return result.scalar_one_or_none() is not None

    async def mark_failed(
        self, user_id: uuid.UUID, *, fence: datetime.datetime
    ) -> bool:
        """Record a failed run (the previous body, if any, is kept)."""
        result = await self.session.execute(
            update(LearningSummary)
            .where(
                LearningSummary.user_id == user_id,
                LearningSummary.started_at == fence,
                LearningSummary.status == "generating",
            )
            .values(status="failed")
            .returning(LearningSummary.user_id)
        )
        return result.scalar_one_or_none() is not None

    async def facts(self, user_id: uuid.UUID) -> SummaryFacts:
        lessons_complete = await self.session.scalar(
            select(func.count(Lesson.id))
            .join(Path, Path.id == Lesson.path_id)
            .where(Path.user_id == user_id, Lesson.completed_at.is_not(None))
        )
        paths = await self.session.scalar(
            select(func.count(Path.id)).where(
                Path.user_id == user_id, Path.status == PathStatus.READY
            )
        )
        beats = await self.session.scalar(
            select(func.count(Beat.id)).where(Beat.user_id == user_id)
        )
        return SummaryFacts(
            lessons_complete=lessons_complete or 0,
            paths=paths or 0,
            beats=beats or 0,
        )

    async def digest_paths(self, user_id: uuid.UUID) -> list[DigestPath]:
        rows = await self.session.execute(
            select(Path.id, Path.topic, Path.level)
            .where(Path.user_id == user_id, Path.status == PathStatus.READY)
            .order_by(Path.created_at, Path.id)
        )
        return [DigestPath(id=r.id, topic=r.topic, level=r.level) for r in rows]

    async def digest_lessons(self, user_id: uuid.UUID) -> list[DigestLesson]:
        """Every lesson of the account's ready paths, with completion and misses."""
        missed = (
            select(QuickCheck.lesson_id)
            .join(Attempt, Attempt.quick_check_id == QuickCheck.id)
            .where(Attempt.user_id == user_id, Attempt.is_correct.is_(False))
        )
        rows = await self.session.execute(
            select(
                Lesson.path_id,
                Lesson.title,
                Lesson.completed_at.is_not(None).label("complete"),
                Lesson.id.in_(missed).label("missed"),
            )
            .join(Path, Path.id == Lesson.path_id)
            .where(Path.user_id == user_id, Path.status == PathStatus.READY)
            .order_by(Lesson.path_id, Lesson.position_in_path)
        )
        return [
            DigestLesson(
                path_id=r.path_id, title=r.title, complete=r.complete, missed=r.missed
            )
            for r in rows
        ]

    async def digest_beats(self, user_id: uuid.UUID) -> list[DigestBeat]:
        rows = await self.session.execute(
            select(Beat.topic, Beat.level)
            .where(Beat.user_id == user_id)
            .order_by(Beat.created_at, Beat.id)
        )
        return [DigestBeat(topic=r.topic, level=r.level) for r in rows]
