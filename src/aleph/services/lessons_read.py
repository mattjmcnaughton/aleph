"""Read-side composition for the Lessons API (AL-051, TDD §6).

Sibling to ``services/paths_read.py`` (the paths detail seam AL-050 built): the
lesson detail poll target (``GET /lessons/{id}``) is likewise not a single read.
It polls the orchestrator (poll-as-trigger + effective generation state), derives
the lesson's unlock state over the whole path (the two orthogonal axes,
CONTEXT.md), and — only once the learner has attempted — loads the recorded
Attempt to reveal the keyed answer (W6). That assembly lives here, behind one
function, so the router stays parse/authz/translate (layering: routers ->
services -> repositories/domains).

:func:`lesson_unlock_state` is factored out because the attempt and complete
routes need the same derived unlock axis (locked → 403) without loading the full
content view — one place derives "where this lesson sits on the learner's path".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from aleph.domains.grading import Attempt as GradingAttempt
from aleph.domains.grading import Outcome, grade
from aleph.domains.progression import (
    LessonProgress,
    UnlockState,
    derive_unlock_states,
)
from aleph.models import LessonGenerationState
from aleph.repositories import (
    AttemptRepository,
    LessonRepository,
    QuickCheckRepository,
)

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.models import Lesson
    from aleph.services.generation import GenerationOrchestrator


@dataclass(frozen=True)
class AttemptResultView:
    """The revealed Outcome of a recorded Attempt (post-Attempt only)."""

    selected_index: int
    outcome: Outcome
    correct_index: int
    explanation: str


@dataclass(frozen=True)
class QuickCheckView:
    """The Quick check as shown pre-Attempt: stem + options, no keyed answer (W6)."""

    stem: str
    options: list[str]


@dataclass(frozen=True)
class LessonDetailView:
    """The composed lesson-detail snapshot the router translates to the DTO.

    ``generation_state`` is the **effective** state (stale ``generating`` →
    ``failed``); ``unlock_state`` is derived over the whole path. Content fields
    (``read_passage``, ``quick_check``) are set only when generated;
    ``attempt`` only once the learner has attempted (its sole carrier of the
    keyed answer, W6); ``generation_error`` only when failed.
    """

    lesson: Lesson
    generation_state: LessonGenerationState
    unlock_state: UnlockState
    read_passage: str | None
    quick_check: QuickCheckView | None
    attempt: AttemptResultView | None
    generation_error: str | None


async def lesson_unlock_state(
    session: AsyncSession, *, path_id: uuid.UUID, lesson_id: uuid.UUID
) -> UnlockState | None:
    """Derive one lesson's unlock state over its whole path, or ``None``.

    Loads the path's lessons in ``position_in_path`` order, runs the pure
    progression derivation (``domains/progression``), and returns this lesson's
    state (``locked``/``available``/``complete``). ``None`` if the lesson is not
    among the path's lessons (a raced delete). The attempt/complete routes use
    this to enforce the available-only rule (locked → 403, AL-012 / TDD §6).
    """
    lessons = await LessonRepository(session).list_for_path(path_id)
    states = derive_unlock_states(
        [
            LessonProgress(
                position_in_path=lesson.position_in_path,
                completed_at=lesson.completed_at,
            )
            for lesson in lessons
        ]
    )
    for lesson, state in zip(lessons, states, strict=True):
        if lesson.id == lesson_id:
            return state
    return None


async def load_lesson_detail(
    session: AsyncSession,
    orchestrator: GenerationOrchestrator,
    *,
    lesson: Lesson,
    user_id: uuid.UUID,
) -> LessonDetailView | None:
    """Poll + assemble the detail view for an already-owned lesson, or ``None``.

    ``poll_lesson`` both spawns the idempotent resume (poll-as-trigger, §5.4 —
    which also refills the prefetch window, so *viewing* a lesson advances
    prefetch) and returns the lesson's **effective** generation state. ``None``
    means the lesson vanished between the caller's ownership read and the poll (a
    raced path delete) — the router maps that to ``404``.

    The unlock state is derived over the whole path. When the lesson is
    ``generated`` its Read passage and Quick check are attached; the keyed answer
    is revealed **only** through ``attempt`` — loaded solely when the learner has
    a recorded Attempt, and re-graded from the stored ``selected_index`` (never
    trusting the ``attempts.is_correct`` denormalization, AL-012).
    """
    effective_state = await orchestrator.poll_lesson(lesson.id)
    if effective_state is None:
        return None

    unlock_state = await lesson_unlock_state(
        session, path_id=lesson.path_id, lesson_id=lesson.id
    )
    if unlock_state is None:
        return None

    quick_check_view: QuickCheckView | None = None
    attempt_view: AttemptResultView | None = None
    read_passage: str | None = None

    # Gate ALL generated-only content on the **effective** state (TN-1), not on
    # the mere existence of a quick-check row: a lesson can transiently hold a
    # quick-check row while its effective state is not ``generated`` (a
    # mid-transition or hand-crafted row), and the api.md invariant is that
    # ``read_passage`` / ``quick_check`` / ``attempt`` are non-null **only** when
    # ``generation_state == generated``. Because ``generated`` is terminal and
    # immutable, gating here makes that invariant hold by construction — never a
    # transient window where content leaks while the state still reads
    # ``generating`` (or has gone stale → ``failed``).
    if effective_state is LessonGenerationState.GENERATED:
        quick_check = await QuickCheckRepository(session).get_for_lesson(lesson.id)
        if quick_check is not None:
            read_passage = lesson.read_passage
            quick_check_view = QuickCheckView(
                stem=quick_check.stem, options=list(quick_check.options)
            )
            attempt = await AttemptRepository(session).get(
                quick_check_id=quick_check.id, user_id=user_id
            )
            if attempt is not None:
                # Re-derive the Outcome from the recorded (first-wins) selected
                # index — never the stored ``is_correct`` bool, a write-time cache
                # that could drift from the keyed answer (AL-012/domains/grading).
                outcome = grade(
                    GradingAttempt(selected_index=attempt.selected_index),
                    correct_index=quick_check.correct_index,
                )
                attempt_view = AttemptResultView(
                    selected_index=attempt.selected_index,
                    outcome=outcome,
                    correct_index=quick_check.correct_index,
                    explanation=quick_check.explanation,
                )

    return LessonDetailView(
        lesson=lesson,
        generation_state=effective_state,
        unlock_state=unlock_state,
        read_passage=read_passage,
        quick_check=quick_check_view,
        attempt=attempt_view,
        generation_error=lesson.generation_error,
    )
