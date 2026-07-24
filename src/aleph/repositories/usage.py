"""Data access for per-account usage counts backing the daily rate limits.

The rate limiter (``services.rate_limit``) enforces caps by counting *real
rows* the learner already created today rather than keeping a separate counter:
paths are counted by their ``created_at``, lesson generations by the
``generation_started_at`` stamp a claim writes (TDD §10). Because a re-claim
re-stamps the same row, this counts each lesson's *latest* claim, not every
attempt — same-day retries of one lesson consume one quota unit (an accepted
undercount vs habagou's per-attempt counter; see ``services.rate_limit``).
Counting persisted rows means the caps survive process restarts and
multi-process deployments for free — there is no in-memory state to lose or
shard — at the cost of one small ``COUNT`` query per check.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from aleph.models import Lesson, Path

if TYPE_CHECKING:
    import datetime
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession


class UsageRepository:
    """Counts a learner's billable events since an instant, for rate limiting.

    Constructed per-request with the caller's :class:`AsyncSession` (repository
    convention); never opens or commits a transaction. Structurally satisfies
    the ``UsageCounter`` protocol the limiter depends on.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def count_paths_created_since(
        self, *, user_id: uuid.UUID, since: datetime.datetime
    ) -> int:
        """How many paths ``user_id`` created at/after ``since``.

        Path creation is the outline-generation trigger, so ``created_at`` is
        the billable instant to count against the daily path cap.
        """
        result = await self.session.execute(
            select(func.count())
            .select_from(Path)
            .where(Path.user_id == user_id, Path.created_at >= since)
        )
        return result.scalar_one()

    async def count_path_outline_generations_since(
        self, *, user_id: uuid.UUID, since: datetime.datetime
    ) -> int:
        """Count ``user_id``'s paths whose outline was (re)claimed since ``since``.

        Unlike :meth:`count_paths_created_since` (keyed on ``created_at``, so a
        row is counted once by its creation day), this counts the ``paths`` rows
        whose ``generation_started_at`` — the stamp an outline claim writes — is
        at/after ``since``. Because a retry re-claims and **re-stamps** the same
        row, this counts each path's *latest* outline attempt, so it bounds
        outline generations at *distinct paths with an attempt today*: it backs
        the retry cap (``check_outline_generation``), which path creation cannot
        (a retry inserts no row, so a created-rows count never moves on retry).

        Same-path retry loops still slip through — repeated retries of one path
        overwrite the one stamp, so the row counts once — an accepted MVP
        limitation documented in ``services.rate_limit``.
        """
        result = await self.session.execute(
            select(func.count())
            .select_from(Path)
            .where(
                Path.user_id == user_id,
                Path.generation_started_at >= since,
            )
        )
        return result.scalar_one()

    async def count_lesson_generations_since(
        self, *, user_id: uuid.UUID, since: datetime.datetime
    ) -> int:
        """Count ``user_id``'s lessons whose generation was triggered since ``since``.

        A lesson's ``generation_started_at`` is stamped the moment a claim wins
        (``LessonRepository._claim``), i.e. the instant the billed model call is
        triggered — counting rows with a stamp at/after ``since`` counts today's
        lesson-generation attempts, whether or not each later succeeded (a failed
        generation still consumed spend, so it still counts). Lessons join to the
        owning path for the learner filter.
        """
        result = await self.session.execute(
            select(func.count())
            .select_from(Lesson)
            .join(Path, Lesson.path_id == Path.id)
            .where(
                Path.user_id == user_id,
                Lesson.generation_started_at >= since,
            )
        )
        return result.scalar_one()
