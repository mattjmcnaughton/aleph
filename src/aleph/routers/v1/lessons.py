"""Lessons API: poll a lesson, generate/retry, attempt, complete (AL-051, TDD §6).

The learner-facing surface over one lesson's content and progress, layered like
the paths router (CLAUDE.md: routers -> services -> (orchestrator, repositories,
domains)). Every route is session-cookie protected (``get_current_user`` → ``401``
via the shared envelope) and addresses by UUID; a lesson on another learner's
path reads as ``404`` (its existence is not disclosed, TDD §6), resolved once by
the shared ``OwnedLesson`` dependency (``LessonRepository.get_for_user``).

**Trigger + poll (§5.4/D5).** ``POST /lessons/{id}/generate`` *triggers*
generation and returns ``202``; the client polls ``GET /lessons/{id}`` (itself a
trigger — the poll spawns the idempotent resume + refills the prefetch window, so
viewing advances prefetch) until ``generation_state`` resolves. ``attempt`` and
``complete`` are synchronous state changes, not generation triggers.

**Answer-hiding (W6, TDD §6).** ``GET`` never serializes the keyed answer before
an Attempt: ``correct_index``/``explanation`` live only inside the ``attempt``
object, which is ``null`` until the learner records an Attempt. Grading is
server-side (``domains/grading``), re-derived from the stored selected index —
the ``attempts.is_correct`` column is a denormalization, never trusted (AL-012).

**The orchestrator is the module-level singleton** (``generation_orchestrator``),
same instance AL-050 imports and AL-041's lifespan binds — so every background
trigger routes through the task registry + concurrency semaphore.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003 - FastAPI resolves route-param annotations.

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import (  # noqa: TC002 - FastAPI resolves annotations.
    AsyncSession,
)

from aleph import events
from aleph.authz import is_admin
from aleph.config import settings
from aleph.db import get_session
from aleph.dependencies import get_current_user
from aleph.domains.grading import Attempt as GradingAttempt
from aleph.domains.grading import Outcome, grade, outcome_of_record
from aleph.domains.progression import UnlockState
from aleph.dtos.lessons import (
    AttemptRequest,
    AttemptResultDTO,
    CompleteLessonResponse,
    GenerateLessonResponse,
    LessonDetailResponse,
    QuickCheckDTO,
)
from aleph.models import (  # noqa: TC001 - FastAPI resolves annotations.
    Lesson,
    User,
)
from aleph.repositories import AttemptRepository, LessonRepository, QuickCheckRepository
from aleph.services.generation import generation_orchestrator
from aleph.services.lessons_read import (
    lesson_unlock_state,
    load_lesson_detail,
)
from aleph.services.rate_limit import build_daily_rate_limiter

if TYPE_CHECKING:
    from aleph.services.lessons_read import LessonDetailView

router = APIRouter(prefix="/api/v1", tags=["lessons"])

CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]


def _lesson_not_found() -> HTTPException:
    """A ``404`` for a lesson the caller does not own or that does not exist.

    Ownership failures return ``404`` (not ``403``) so a learner cannot probe
    which UUIDs belong to others (TDD §6). Rendered through the shared envelope
    as ``{"error": {"code": "not_found", ...}}``.
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="lesson not found"
    )


def _lesson_locked() -> HTTPException:
    """A ``403`` for an attempt/complete on a locked lesson (TDD §6).

    The lesson's state is visible on ``GET`` (its existence is not hidden from
    the owner), but a **locked** lesson can be neither attempted nor completed
    (AL-012 executor-routed rule). The two acting routes differ on the other
    states: attempt gates on *not locked* (a complete lesson stays attemptable),
    while complete is available-only (an already-complete lesson is an idempotent
    no-op, never a ``403``).
    """
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="lesson is locked"
    )


def _lesson_not_generated() -> HTTPException:
    """A ``409`` for an attempt on a lesson whose Quick check does not exist yet.

    An available-but-ungenerated lesson (the two axes are orthogonal, CONTEXT.md)
    has no Quick check to answer — attempting it is a conflict with its current
    generation state, not a validation error, so ``409`` (not ``422``/``404``).
    """
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="lesson content has not been generated yet",
    )


async def get_owned_lesson(
    lesson_id: UUID, user: CurrentUser, session: Session
) -> Lesson:
    """Resolve ``lesson_id`` only if it is on a path the caller owns, else ``404``.

    The single ownership seam behind every lesson route (TDD §6):
    ``LessonRepository.get_for_user`` joins ``lessons -> paths`` and matches the
    owner, so a lesson on another learner's path is indistinguishable from a
    missing one. ``get_current_user`` runs first, so an anonymous request is
    rejected with ``401`` before ownership is ever considered.
    """
    lesson = await LessonRepository(session).get_for_user(
        lesson_id=lesson_id, user_id=user.id
    )
    if lesson is None:
        raise _lesson_not_found()
    return lesson


OwnedLesson = Annotated[Lesson, Depends(get_owned_lesson)]


def _detail_response(view: LessonDetailView) -> LessonDetailResponse:
    """Translate the composed read-seam view to the wire DTO."""
    lesson = view.lesson
    quick_check = (
        QuickCheckDTO(stem=view.quick_check.stem, options=view.quick_check.options)
        if view.quick_check is not None
        else None
    )
    attempt = (
        AttemptResultDTO(
            selected_index=view.attempt.selected_index,
            outcome=view.attempt.outcome,
            correct_index=view.attempt.correct_index,
            explanation=view.attempt.explanation,
        )
        if view.attempt is not None
        else None
    )
    return LessonDetailResponse(
        id=lesson.id,
        path_id=lesson.path_id,
        title=lesson.title,
        position_in_path=lesson.position_in_path,
        position_in_unit=lesson.position_in_unit,
        generation_state=view.generation_state,
        unlock_state=view.unlock_state,
        read_passage=view.read_passage,
        quick_check=quick_check,
        attempt=attempt,
        generation_error=view.generation_error,
    )


@router.get("/lessons/{lesson_id}")
async def get_lesson(
    lesson: OwnedLesson, user: CurrentUser, session: Session
) -> LessonDetailResponse:
    """Poll target: generation state, unlock state, content, and Attempt (§6).

    Ownership is resolved by ``OwnedLesson`` (``404`` otherwise). The composition
    — poll-as-trigger resume + prefetch advance, effective generation state,
    derived unlock state, content when generated, and the revealed Attempt only
    once one exists (W6) — lives in ``services.lessons_read.load_lesson_detail``;
    this route only translates the result. A ``None`` view means the lesson was
    deleted between the ownership read and the poll (a raced path delete) →
    ``404``.
    """
    view = await load_lesson_detail(
        session, generation_orchestrator, lesson=lesson, user_id=user.id
    )
    if view is None:
        raise _lesson_not_found()
    # The lesson was served to its owner (PRD §5.7 "lesson viewed"). Polling emits
    # one event per view; the metrics (path-start = view lesson 1, continuation =
    # view lesson N+1) count distinct positions, so repeated polls are harmless.
    events.emit_lesson_viewed(
        account_id=user.id,
        path_id=lesson.path_id,
        lesson_id=lesson.id,
        position_in_path=lesson.position_in_path,
    )
    return _detail_response(view)


@router.post("/lessons/{lesson_id}/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_lesson(
    lesson: OwnedLesson, user: CurrentUser, session: Session
) -> GenerateLessonResponse:
    """Ensure/retry a lesson's generation → ``202 {id}`` (W8; trigger + poll).

    Ownership via ``OwnedLesson`` (``404`` otherwise). A billed trigger, so it
    carries the daily lesson-generation cap (``check_lesson_generation``, admins
    exempt, TDD §10) *before* triggering — a breach raises ``429`` with the
    ``rate_limited`` envelope. On pass the orchestrator's public
    ``trigger_lesson_generation`` fires ``retry_lesson`` through the registry-bound
    spawn: it *ensures* a chain-head ``ungenerated`` lesson the learner reached and
    *retries* a chain-head ``failed`` one (the chain-head ordering guard lives in
    the orchestrator, §5.2/§5.5), then refills the prefetch window. The request
    returns immediately; the client polls ``GET /lessons/{id}``. A trigger on a
    terminal ``generated`` lesson or a non-chain-head one still returns ``202`` but
    is a no-op for that lesson (only the window is advanced) — never an error to
    model in the route.
    """
    limiter = build_daily_rate_limiter(session)
    await limiter.check_lesson_generation(
        user_id=user.id, is_admin=is_admin(user, settings)
    )
    generation_orchestrator.trigger_lesson_generation(lesson.id)
    return GenerateLessonResponse(id=lesson.id)


@router.post("/lessons/{lesson_id}/attempt")
async def attempt_lesson(
    body: AttemptRequest, lesson: OwnedLesson, user: CurrentUser, session: Session
) -> AttemptResultDTO:
    """Record an Attempt (first wins) and return the graded Outcome (W6, §4/§6).

    Ownership via ``OwnedLesson`` (``404`` otherwise). Guards, in order:

    * a **locked** lesson → ``403``. Attempt gates on *not locked*, **not**
      available-only: a **complete** lesson stays attemptable (completion is
      orthogonal to the Attempt — a learner may complete a lesson and still answer
      its Quick check, AL-012 / TN-3). Only a locked (later / not-yet-reached)
      lesson is refused; its state is still readable on ``GET``.
    * an ungenerated lesson (no Quick check yet) → ``409``.

    Grading is deterministic and server-side (``domains/grading``). The Attempt is
    recorded first-wins (``AttemptRepository.record``, ``ON CONFLICT DO NOTHING``),
    so a second submit never overwrites the first; the returned row is the Attempt
    **of record**, and the Outcome is re-derived from *its* stored
    ``selected_index`` (never the ``is_correct`` denormalization, AL-012). The
    response reveals the keyed ``correct_index`` + ``explanation`` — this endpoint
    is the reveal boundary, mirroring the ``attempt`` object on ``GET``.
    """
    unlock_state = await lesson_unlock_state(
        session, path_id=lesson.path_id, lesson_id=lesson.id
    )
    if unlock_state is None:
        raise _lesson_not_found()
    if unlock_state is UnlockState.LOCKED:
        raise _lesson_locked()

    quick_check = await QuickCheckRepository(session).get_for_lesson(lesson.id)
    if quick_check is None:
        raise _lesson_not_generated()

    submitted = GradingAttempt(selected_index=body.selected_index)
    submitted_is_correct = (
        grade(submitted, correct_index=quick_check.correct_index) is Outcome.CORRECT
    )
    attempt_row, created = await AttemptRepository(session).record(
        quick_check_id=quick_check.id,
        user_id=user.id,
        selected_index=body.selected_index,
        is_correct=submitted_is_correct,
    )
    await session.commit()

    # Resolve the Attempt of record + its Outcome through the domain's first-wins
    # encoding (``domains/grading.outcome_of_record``, the stated rule). The
    # atomic ``record`` (``ON CONFLICT DO NOTHING``) is the durable first-wins and
    # tells us via ``created`` whether a prior Attempt already existed: when it
    # did, the stored row is that prior first answer and ``submitted`` is ignored
    # for scoring; otherwise the submission is the record. The stored
    # ``is_correct`` is a metrics cache, never the truth (AL-012).
    prior = (
        None if created else GradingAttempt(selected_index=attempt_row.selected_index)
    )
    of_record, outcome = outcome_of_record(
        prior=prior, submitted=submitted, correct_index=quick_check.correct_index
    )
    # The Attempt with its Outcome of record (W6, PRD §5.7). This event is the
    # activation gate: a completed lesson counts toward "activated" only if its
    # Quick check was also attempted (§7). Emitted **only on the first-wins
    # Attempt** (``created``): a repeat submit is not an Attempt (CONTEXT.md /
    # AL-012), so it must not re-emit — a second event would double-count the
    # correctness guardrail's denominator and the activation gate. ``outcome`` is
    # the recorded (first) Outcome regardless, so the single event is truthful.
    if created:
        events.emit_quick_check_attempted(
            account_id=user.id,
            path_id=lesson.path_id,
            lesson_id=lesson.id,
            position_in_path=lesson.position_in_path,
            outcome=outcome.value,
        )
    return AttemptResultDTO(
        selected_index=of_record.selected_index,
        outcome=outcome,
        correct_index=quick_check.correct_index,
        explanation=quick_check.explanation,
    )


@router.post("/lessons/{lesson_id}/complete")
async def complete_lesson(
    lesson: OwnedLesson, user: CurrentUser, session: Session
) -> CompleteLessonResponse:
    """Mark the available lesson complete (non-gating) → ``200`` (§4/§6).

    Ownership via ``OwnedLesson`` (``404`` otherwise). Completion is orthogonal to
    the Quick-check Outcome (non-gating, CONTEXT.md) and to generation state, but
    it is gated on the **unlock** axis: only the available lesson may be completed
    (AL-012 executor-routed rule). Derived cases:

    * **locked** → ``403`` (a later or not-yet-reached lesson; you cannot skip
      ahead — a generated-but-locked lesson is rejected here too).
    * **complete** → idempotent ``200`` no-op: re-completing does not re-stamp
      ``completed_at`` or move the window.
    * **available** → mark complete, commit, then advance the prefetch window
      **after** the commit (``on_lesson_completed`` recomputes ``first_incomplete``
      in a fresh session, AL-040 docstring note), so the newly-unlocked next
      lesson begins prefetching.

    The window advance is fired **only** when this request performed the
    transition (``mark_completed_and_finalize`` returns ``newly_completed``). A
    concurrent second complete that raced past the ``COMPLETE`` early-return
    re-stamps nothing (the repo's ``completed_at IS NULL`` guard, TN-4) and must
    not re-fire the advance either. Path completion is derived **atomically** in
    the repository (under the path lock), so ``path_completed`` fires exactly once
    — never a double-emit if two lessons on a path resolve concurrently.
    """
    unlock_state = await lesson_unlock_state(
        session, path_id=lesson.path_id, lesson_id=lesson.id
    )
    if unlock_state is None:
        raise _lesson_not_found()
    if unlock_state is UnlockState.LOCKED:
        raise _lesson_locked()
    if unlock_state is UnlockState.COMPLETE:
        return CompleteLessonResponse(id=lesson.id, unlock_state=UnlockState.COMPLETE)

    lessons_repo = LessonRepository(session)
    # Mark complete and derive path completion in one fenced, path-locked step:
    # ``path_now_complete`` is True only for the completion that flipped the last
    # incomplete lesson, and ``lesson_count`` is a ``count()`` (no list hydration).
    (
        newly_completed,
        path_now_complete,
        lesson_count,
    ) = await lessons_repo.mark_completed_and_finalize(
        lesson_id=lesson.id, path_id=lesson.path_id
    )
    await session.commit()
    if newly_completed:
        # Emit the (now durable) transition events BEFORE the prefetch advance:
        # ``on_lesson_completed`` can raise, and a committed completion must never
        # lose its product event to that. Only on the real transition, so counts
        # are not inflated by an idempotent re-complete (W1, PRD §5.7).
        events.emit_lesson_completed(
            account_id=user.id,
            path_id=lesson.path_id,
            lesson_id=lesson.id,
            position_in_path=lesson.position_in_path,
        )
        # Path completion derives from lesson state (PRD §5.4): decided atomically
        # above (last incomplete lesson just flipped), so this fires exactly once
        # for the path (W3), never a double-emit race.
        if path_now_complete:
            events.emit_path_completed(
                account_id=user.id,
                path_id=lesson.path_id,
                lesson_count=lesson_count,
            )
        # Advance the prefetch window last (after the commit so ``first_incomplete``
        # already reflects it, AL-040 note); only on the real transition, so a
        # raced double-complete never re-advances the window on a no-op write.
        await generation_orchestrator.on_lesson_completed(lesson.id)
    return CompleteLessonResponse(id=lesson.id, unlock_state=UnlockState.COMPLETE)
