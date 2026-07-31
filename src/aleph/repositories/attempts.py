"""Data access for attempts (a learner answering a Quick check; first wins)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aleph.models import Attempt, Lesson, QuickCheck

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class LessonAnswer:
    """One lesson's recorded Attempt next to its Quick check's keyed answer.

    Exactly the two numbers :func:`aleph.domains.grading.grade` needs, and
    deliberately not the ``attempts.is_correct`` column — that is a write-time
    denormalization which can drift from the keyed answer, so the Outcome is
    always re-derived (AL-012's rule, followed by ``services/lessons_read.py``
    and the tutor context seam alike). The caller grades; this reports.
    """

    selected_index: int
    correct_index: int


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

    async def list_answers_for_path(
        self, path_id: uuid.UUID
    ) -> dict[uuid.UUID, LessonAnswer]:
        """Every attempted lesson on the path, keyed by ``lesson_id`` (2B §5.2).

        The **Outcome** half of shaping scope (PRD §5.2): the shaping tutor sees
        each attempted lesson's Outcome, so the seam needs the whole path's
        answers in one read rather than one query per lesson. Unattempted
        lessons are simply absent — ``lesson_id in answers`` is the same fact as
        ``has_attempt`` from
        :meth:`~aleph.repositories.lessons.LessonRepository.list_for_path_with_engagement`,
        which stays the D2 boundary's source of truth.

        Not filtered by user, for
        :meth:`~aleph.repositories.lessons.LessonRepository.list_for_path_with_engagement`'s
        reason: a lesson belongs to exactly one path and a path to exactly one
        account, so every Attempt on its Quick check is that learner's.

        Returns the selected and keyed indexes, never ``attempts.is_correct``:
        the Outcome is re-derived through ``domains/grading`` (AL-012).
        """
        result = await self.session.execute(
            select(
                QuickCheck.lesson_id, Attempt.selected_index, QuickCheck.correct_index
            )
            .select_from(QuickCheck)
            .join(Attempt, Attempt.quick_check_id == QuickCheck.id)
            .join(Lesson, Lesson.id == QuickCheck.lesson_id)
            .where(Lesson.path_id == path_id)
        )
        return {
            lesson_id: LessonAnswer(
                selected_index=selected_index, correct_index=correct_index
            )
            for lesson_id, selected_index, correct_index in result.all()
        }
