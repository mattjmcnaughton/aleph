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

Phase 2 adds one counter of the same shape: tutor messages, counted over the
learner's **live** ``messages`` rows (AL-220, §7/D8). Its own quirk — "new
conversation" deletes those rows and so refunds quota — is recorded rather than
fixed, because the cap ships disabled; see ``services.rate_limit``.

Phase 3 adds one more, of the ``count_path_outline_generations_since`` shape
rather than the plain created-rows shape: flashcard drafting runs, counted by
the ``flashcard_draft_runs.started_at`` stamp a claim (re-)writes (TDD §5.2/
D13). Like an outline retry, a drafting retry inserts no new row (D7's sparse,
one-row-per-lesson claim) — only the stamp moves — so this counts *lessons
with a drafting attempt today*, and a same-lesson retry loop still counts once;
see ``services.rate_limit``.

Phase 6 adds the sixth counter, the same ``count_path_outline_generations_since``
shape again: Beat research runs, counted by ``beats.research_started_at`` — the
stamp ``BeatRepository._claim`` (re-)writes on every claim (both the auto and
retry paths, TDD D3/D14). A same-Beat retry loop (a ``failed`` run re-claimed
via ``POST /retry``) overwrites the one stamp and so counts once, the same
accepted MVP shape; see ``services.rate_limit``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from aleph.models import (
    Beat,
    Conversation,
    ConversationKind,
    FlashcardDraftRun,
    Lesson,
    Message,
    MessageRole,
    Path,
)

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

    async def count_tutor_messages_since(
        self, *, user_id: uuid.UUID, since: datetime.datetime
    ) -> int:
        """Count ``user_id``'s **learner** tutor messages created since ``since``.

        The billed unit of a tutor turn is the learner's question: one question
        buys one reply (Phase 2 §7 / D8), so counting learner rows counts turns
        without double-counting the tutor's own row. Messages join
        conversation → path for the learner filter, the same shape every other
        counter here uses.

        Counting **live rows** is the deliberate Phase 1 pattern, and it carries
        the Phase 1 quirk the PRD already named: "new conversation" deletes the
        conversation (cascading its messages), so a cleared thread refunds
        quota. That is why the refund-proof append-only usage table is the
        recorded precondition for enabling the cap — while
        ``RATE_LIMIT_TUTOR_MESSAGES_PER_DAY`` is 0 this query is never run.

        **Scoped to the in-lesson thread** (Phase 2B): a path now carries two
        conversations, and shaping messages have their own cap (§7). Without the
        kind filter a shaping turn would quietly spend the *tutor's* budget —
        a change in what 2A's cap counts, made by a phase that promised not to
        change 2A behaviour (W21).
        """
        return await self._count_learner_messages(
            user_id=user_id, since=since, kind=ConversationKind.LESSON
        )

    async def count_shaping_messages_since(
        self, *, user_id: uuid.UUID, since: datetime.datetime
    ) -> int:
        """Count ``user_id``'s **learner** shaping messages created since ``since``.

        The Phase 2B twin (TDD §7), over live **shaping** learner-message rows:
        same billed unit (one question buys one reply), same live-row counting,
        the same thread-clear-refunds quirk, and the same recorded precondition
        for ever raising the cap above its default of 0.

        Applied **Additions** need no counter of their own: the lessons they add
        are ordinary generations under ``RATE_LIMIT_LESSON_GENERATIONS_PER_DAY``,
        and path size is bounded by ``MAX_LESSONS_PER_PATH`` at proposal *and*
        apply time (§7).
        """
        return await self._count_learner_messages(
            user_id=user_id, since=since, kind=ConversationKind.SHAPING
        )

    async def count_flashcard_draft_runs_since(
        self, *, user_id: uuid.UUID, since: datetime.datetime
    ) -> int:
        """Count ``user_id``'s lessons whose drafting run was (re)claimed since
        ``since`` (Phase 3 TDD §5.2/D13).

        The ``count_path_outline_generations_since`` shape, not
        ``count_lesson_generations_since``'s: ``flashcard_draft_runs`` is a
        sparse, one-row-per-lesson table (D7) whose ``started_at`` a claim
        **re-stamps** on every (re-)claim, so this counts *distinct lessons
        with a drafting attempt today* — a same-lesson ``failed`` -> retry loop
        overwrites the one stamp and so counts once, the accepted MVP shape
        ``services.rate_limit`` documents. Joins ``flashcard_draft_runs`` ->
        ``lessons`` -> ``paths`` for the learner filter, since the run row
        carries no ``user_id`` of its own (D7: it is keyed on ``lesson_id``
        alone).
        """
        result = await self.session.execute(
            select(func.count())
            .select_from(FlashcardDraftRun)
            .join(Lesson, FlashcardDraftRun.lesson_id == Lesson.id)
            .join(Path, Lesson.path_id == Path.id)
            .where(
                Path.user_id == user_id,
                FlashcardDraftRun.started_at >= since,
            )
        )
        return result.scalar_one()

    async def count_brief_research_runs_since(
        self, *, user_id: uuid.UUID, since: datetime.datetime
    ) -> int:
        """Count ``user_id``'s Beats whose research run was (re)claimed since
        ``since`` (Phase 6 TDD D14).

        The ``count_path_outline_generations_since`` shape: ``research_started_at``
        is stamped by every claim (auto or retry, ``BeatRepository._claim``), so
        this counts *distinct Beats with a research attempt today* — a same-Beat
        retry loop overwrites the one stamp and so counts once, the accepted MVP
        shape ``services.rate_limit`` documents for its siblings. No join needed:
        unlike lessons/drafts, a Beat carries its own ``user_id``.
        """
        result = await self.session.execute(
            select(func.count())
            .select_from(Beat)
            .where(Beat.user_id == user_id, Beat.research_started_at >= since)
        )
        return result.scalar_one()

    async def _count_learner_messages(
        self,
        *,
        user_id: uuid.UUID,
        since: datetime.datetime,
        kind: ConversationKind,
    ) -> int:
        """``user_id``'s live learner rows in threads of ``kind``, since ``since``.

        One query behind both caps: they differ by exactly the conversation kind,
        and two spellings of "count the learner's messages" is how two caps start
        counting subtly different things.
        """
        result = await self.session.execute(
            select(func.count())
            .select_from(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .join(Path, Conversation.path_id == Path.id)
            .where(
                Path.user_id == user_id,
                Conversation.kind == kind,
                Message.role == MessageRole.LEARNER,
                Message.created_at >= since,
            )
        )
        return result.scalar_one()
