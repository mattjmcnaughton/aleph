"""Data access for lessons: atomic claim, stale-aware reads, progress (§5.4/§6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import (
    ColumnElement,
    func,
    select,
    update,
)

from aleph.config import settings
from aleph.models import Lesson, LessonGenerationState, Path, Unit
from aleph.repositories._generation import (
    affected_rows,
    claimable_predicate,
    effective_state_case,
)

if TYPE_CHECKING:
    import datetime
    import uuid
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

# Generation states an auto (prefetch/reconciler) claim may take into
# ``generating``: a never-started lesson, or a stale ``generating`` one whose
# process died (TDD §5.4). ``failed`` is excluded — only the learner's explicit
# retry re-runs a real failure. ``generated`` is terminal (content immutable).
_CLAIMABLE_STATES = (LessonGenerationState.UNGENERATED,)
_RETRY_CLAIMABLE_STATES = (
    LessonGenerationState.UNGENERATED,
    LessonGenerationState.FAILED,
)


@dataclass(frozen=True)
class PathGenerationProgress:
    """Per-path lesson roll-up for the paths API progress summary (§6).

    ``by_state`` counts every lesson by its **effective** generation state — a
    stale ``generating`` lesson is bucketed as ``failed`` (§5.4), matching what a
    reader/claim would decide at the same instant. Every ``LessonGenerationState``
    is present (zeroed when none match). ``total_lessons`` and
    ``generated_lessons`` are derived from ``by_state`` (they cannot disagree);
    ``completed_lessons`` is an independent count (completion is orthogonal to
    generation state).
    """

    completed_lessons: int
    by_state: dict[LessonGenerationState, int]

    @property
    def total_lessons(self) -> int:
        return sum(self.by_state.values())

    @property
    def generated_lessons(self) -> int:
        return self.by_state[LessonGenerationState.GENERATED]


class LessonRepository:
    """Data access for :class:`~aleph.models.Lesson` rows.

    Constructed per-request with the caller's :class:`AsyncSession`; never
    commits — the service layer owns the transaction. ``stale_after_seconds`` is
    the generation stale window (TDD §5.4); it defaults to the configured value
    but a service may inject a different policy (AL-040 wiring point).
    """

    def __init__(
        self, session: AsyncSession, *, stale_after_seconds: float | None = None
    ) -> None:
        self.session = session
        self._stale_after_seconds = (
            stale_after_seconds
            if stale_after_seconds is not None
            else settings.generation_stale_after_seconds
        )

    def _effective_state_expr(self) -> ColumnElement[str]:
        return effective_state_case(
            state_col=Lesson.generation_state,
            started_at_col=Lesson.generation_started_at,
            generating_state=LessonGenerationState.GENERATING,
            failed_state=LessonGenerationState.FAILED,
            stale_after_seconds=self._stale_after_seconds,
        )

    async def create(
        self,
        *,
        unit_id: uuid.UUID,
        path_id: uuid.UUID,
        position_in_path: int,
        position_in_unit: int,
        title: str,
    ) -> Lesson:
        lesson = Lesson(
            unit_id=unit_id,
            path_id=path_id,
            position_in_path=position_in_path,
            position_in_unit=position_in_unit,
            title=title,
        )
        self.session.add(lesson)
        await self.session.flush()
        return lesson

    async def get(self, lesson_id: uuid.UUID) -> Lesson | None:
        return await self.session.get(Lesson, lesson_id)

    async def get_for_user(
        self, *, lesson_id: uuid.UUID, user_id: uuid.UUID
    ) -> Lesson | None:
        """Fetch a lesson only if its path belongs to ``user_id`` (ownership)."""
        result = await self.session.execute(
            select(Lesson)
            .join(Path, Lesson.path_id == Path.id)
            .where(Lesson.id == lesson_id, Path.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def list_for_path(self, path_id: uuid.UUID) -> list[Lesson]:
        """All of a path's lessons in ``position_in_path`` (total) order."""
        result = await self.session.execute(
            select(Lesson)
            .where(Lesson.path_id == path_id)
            .order_by(Lesson.position_in_path)
        )
        return list(result.scalars())

    async def list_for_path_with_effective_state(
        self, path_id: uuid.UUID
    ) -> list[tuple[Lesson, LessonGenerationState]]:
        """A path's lessons paired with their **effective** state, in one query.

        The §6 poll target (``GET /paths/{id}``) needs each lesson's effective
        generation state (stale ``generating`` → failed). Computing that in SQL
        here — one grouped-free query over the path — avoids N per-id
        :meth:`effective_state` round-trips for a 30-lesson path.
        """
        result = await self.session.execute(
            select(Lesson, self._effective_state_expr().label("effective_state"))
            .where(Lesson.path_id == path_id)
            .order_by(Lesson.position_in_path)
        )
        return [
            (lesson, LessonGenerationState(state)) for lesson, state in result.all()
        ]

    # -- claim ------------------------------------------------------------- #

    async def claim_for_generation(
        self, lesson_id: uuid.UUID
    ) -> datetime.datetime | None:
        """Atomically claim a lesson for generation (auto path).

        Wins iff the lesson is ``ungenerated`` or a stale ``generating`` (crash
        recovery). The ``UPDATE ... WHERE ... RETURNING`` is the concurrency
        control: exactly one caller flips the row under Postgres' row lock.

        Returns the **fencing token** (the ``generation_started_at`` stamp this
        claim wrote) on a win, or ``None`` if another caller already holds it.
        Pass the token back to :meth:`mark_generated`/:meth:`mark_failed` so a
        stalled worker that lost its claim to a re-claim cannot overwrite the
        fresh one.

        **Commit immediately.** ``generation_started_at`` is set to
        ``func.now()`` = the transaction's start timestamp, and the row lock is
        held until commit. A claim left open in a long transaction both blocks
        every competitor on the row and freezes the stale clock at the
        transaction's start — so a short claim-then-commit transaction is
        load-bearing (TDD §5.4).
        """
        return await self._claim(lesson_id, _CLAIMABLE_STATES)

    async def claim_for_retry(self, lesson_id: uuid.UUID) -> datetime.datetime | None:
        """Atomically claim for an explicit learner retry (POST .../generate).

        Same as :meth:`claim_for_generation` (including the fencing-token return
        and the commit-immediately requirement) but additionally re-claims a
        ``failed`` lesson — the only loop that re-runs a real failure.
        """
        return await self._claim(lesson_id, _RETRY_CLAIMABLE_STATES)

    async def _claim(
        self, lesson_id: uuid.UUID, states: tuple[LessonGenerationState, ...]
    ) -> datetime.datetime | None:
        result = await self.session.execute(
            update(Lesson)
            .where(
                Lesson.id == lesson_id,
                claimable_predicate(
                    state_col=Lesson.generation_state,
                    started_at_col=Lesson.generation_started_at,
                    claimable_states=states,
                    generating_state=LessonGenerationState.GENERATING,
                    stale_after_seconds=self._stale_after_seconds,
                ),
            )
            # updated_at bumped explicitly: a Core UPDATE bypasses the ORM
            # ``onupdate`` hook (AL-010 landmine).
            .values(
                generation_state=LessonGenerationState.GENERATING,
                generation_started_at=func.now(),
                generation_error=None,
                updated_at=func.now(),
            )
            .returning(Lesson.generation_started_at)
        )
        return result.scalar_one_or_none()

    # -- transitions ------------------------------------------------------- #

    async def mark_generated(
        self, *, lesson_id: uuid.UUID, read_passage: str, fence: datetime.datetime
    ) -> bool:
        """Record generated content (terminal; content immutable, §4).

        Guarded: writes only while the row is still ``generating`` **and** its
        ``generation_started_at`` equals ``fence`` (the token from the claim).
        The state guard blocks writes to a terminal row; the fence blocks a
        stalled worker whose claim was already re-claimed by a fresh one (same
        state, different stamp). Returns ``True`` iff this caller still owned the
        claim — ``False`` means it lost and the write was a no-op.
        """
        return await self._guarded_mark(
            lesson_id,
            fence,
            generation_state=LessonGenerationState.GENERATED,
            read_passage=read_passage,
            generated_at=func.now(),
            generation_error=None,
        )

    async def mark_failed(
        self, *, lesson_id: uuid.UUID, error: str, fence: datetime.datetime
    ) -> bool:
        """Record a generation failure (retryable). Same fencing as
        :meth:`mark_generated`: a late mark after a re-claim is a no-op."""
        return await self._guarded_mark(
            lesson_id,
            fence,
            generation_state=LessonGenerationState.FAILED,
            generation_error=error,
        )

    async def _guarded_mark(
        self,
        lesson_id: uuid.UUID,
        fence: datetime.datetime,
        **values: object,
    ) -> bool:
        result = await self.session.execute(
            update(Lesson)
            .where(
                Lesson.id == lesson_id,
                Lesson.generation_state == LessonGenerationState.GENERATING,
                Lesson.generation_started_at == fence,
            )
            .values(updated_at=func.now(), **values)
        )
        return affected_rows(result) > 0

    async def mark_completed(self, lesson_id: uuid.UUID) -> bool:
        """Mark a lesson complete (non-gating; drives derived unlock state).

        Not a generation transition — completion is orthogonal to
        ``generation_state`` — so it carries no state guard or fence. It **is**
        guarded on ``completed_at IS NULL`` so completion is durably idempotent:
        the ``UPDATE`` stamps ``completed_at`` only on the first call, and returns
        whether *this* call performed the transition. Two concurrent completes
        serialize on the row lock — exactly one sees ``completed_at IS NULL`` and
        wins; the other matches no row (TN-4), so ``completed_at`` is stamped once
        and never re-stamped. The caller uses the return to fire the post-commit
        prefetch advance only on the real transition.
        """
        result = await self.session.execute(
            update(Lesson)
            .where(Lesson.id == lesson_id, Lesson.completed_at.is_(None))
            .values(completed_at=func.now(), updated_at=func.now())
        )
        return affected_rows(result) > 0

    # -- stale-aware reads (§5.4/§6) --------------------------------------- #

    async def effective_state(
        self, lesson_id: uuid.UUID
    ) -> LessonGenerationState | None:
        """The lesson's effective state, treating a stale ``generating`` as failed."""
        result = await self.session.execute(
            select(self._effective_state_expr()).where(Lesson.id == lesson_id)
        )
        value = result.scalar_one_or_none()
        return LessonGenerationState(value) if value is not None else None

    async def first_incomplete(self, path_id: uuid.UUID) -> Lesson | None:
        """The lowest-``position_in_path`` lesson with no ``completed_at``.

        The progression anchor (the learner's current lesson); ``None`` when the
        whole path is complete.
        """
        result = await self.session.execute(
            select(Lesson)
            .where(Lesson.path_id == path_id, Lesson.completed_at.is_(None))
            .order_by(Lesson.position_in_path)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def generated_passages_before(
        self, *, path_id: uuid.UUID, position_in_path: int
    ) -> list[tuple[str, str, str]]:
        """Generated Read passages of lessons before ``position_in_path``, in order.

        The continuity context for generating a later lesson (D7/§5.2): only
        already-``generated`` passages, ascending by total position. Each row is
        ``(unit_title, lesson_title, read_passage)`` — the unit title is joined in
        so the continuity prompt can prefix each passage with its real
        ``[Unit / Lesson]`` locator (§5.2), not the lesson title twice.
        """
        result = await self.session.execute(
            select(Unit.title, Lesson.title, Lesson.read_passage)
            .join(Unit, Lesson.unit_id == Unit.id)
            .where(
                Lesson.path_id == path_id,
                Lesson.position_in_path < position_in_path,
                Lesson.generation_state == LessonGenerationState.GENERATED,
            )
            .order_by(Lesson.position_in_path)
        )
        return [
            (unit_title, lesson_title, passage)
            for unit_title, lesson_title, passage in result.all()
        ]

    async def progress_summaries(
        self, path_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, PathGenerationProgress]:
        """Per-path lesson roll-ups for the paths API (§6), in one grouped query.

        Every requested path id is present (zeroed when it has no lessons).
        Stale ``generating`` lessons count as ``failed`` (§5.4).
        """
        if not path_ids:
            return {}

        summaries: dict[uuid.UUID, dict[LessonGenerationState, int]] = {
            path_id: {state: 0 for state in LessonGenerationState}
            for path_id in path_ids
        }
        completed: dict[uuid.UUID, int] = dict.fromkeys(path_ids, 0)

        effective = self._effective_state_expr()
        state_rows = await self.session.execute(
            select(
                Lesson.path_id,
                effective.label("effective_state"),
                func.count(),
                func.count(Lesson.completed_at),
            )
            .where(Lesson.path_id.in_(path_ids))
            .group_by(Lesson.path_id, effective)
        )
        for path_id, state_value, count, completed_count in state_rows.all():
            summaries[path_id][LessonGenerationState(state_value)] = count
            completed[path_id] += completed_count

        return {
            path_id: PathGenerationProgress(
                completed_lessons=completed[path_id],
                by_state=by_state,
            )
            for path_id, by_state in summaries.items()
        }
