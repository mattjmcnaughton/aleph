"""Flashcards API: drafting, the review queue, its summary, and grading
(Phase 3 TDD §5.2-6).

Ticket 5 shipped `GET /reviews/summary`, `GET /reviews/queue`, `POST /reviews`.
This ticket adds the three drafting routes — `POST
/lessons/{id}/flashcard-drafts`, `GET .../flashcard-drafts`, `POST
.../flashcard-drafts/keep` — to the **same router**, following
`routers/v1/progress.py`'s conventions verbatim: session-cookie auth
(`get_current_user` -> `401` through the shared envelope), the router-level
flag gate (inherited by construction, TDD D10's whole point), and
404-never-403 everywhere ownership is at stake.

**The flag gate lives here, not in `services/feature_flags.py`.** Every other
flag gate (`require_tutor_enabled`, `require_shaping_enabled`,
`require_streaks_enabled`) is defined in its own router module; this one
follows suit (TDD D10 — it was defined in `services/feature_flags.py` only as
a placeholder until this file existed).

**Read-only except two writes.** `GET /reviews/summary` and `GET
/reviews/queue` hold no `session.commit()` — the derivation they read is a
pure function of already-committed state (D3). `POST /reviews` and `POST
.../flashcard-drafts/keep` are the two writes: each commits once, after its
service function returns, so its two-statement transaction (review-append +
projection-update, §5.4; keep-update + discard-delete, §5.2) lands atomically.
`POST .../flashcard-drafts` triggers a **background** claim + run
(`FlashcardDraftingService.trigger_draft_run`) and returns `202` with no
session write of its own — the claim commits inside the spawned task, not the
request (§5.2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003 - FastAPI resolves query-param annotations.

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import (  # noqa: TC002 - FastAPI resolves annotations.
    AsyncSession,
)

from aleph.authz import is_admin
from aleph.config import settings
from aleph.db import get_session
from aleph.dependencies import get_current_user
from aleph.dtos.flashcards import (
    DegradedCitationDTO,
    FlashcardDraftCardDTO,
    FlashcardDraftsResponse,
    GradeCardRequest,
    GradeCardResponse,
    KeepFlashcardDraftsRequest,
    KeepFlashcardDraftsResponse,
    LinkedCitationDTO,
    PathDueDTO,
    QueueCardDTO,
    ReviewQueueResponse,
    ReviewSummaryResponse,
    TriggerFlashcardDraftsResponse,
)
from aleph.dtos.progress import TzOffsetMinutes  # noqa: TC001 - FastAPI resolves it.
from aleph.models import Lesson, User  # noqa: TC001 - FastAPI resolves annotations.
from aleph.repositories import LessonRepository
from aleph.services.feature_flags import FeatureFlag, FeatureFlagService
from aleph.services.flashcard_drafting import (
    flashcard_drafting_service,
    keep_flashcard_drafts,
    load_flashcard_drafts,
)
from aleph.services.rate_limit import build_daily_rate_limiter
from aleph.services.reviews import grade_card, load_review_queue, load_review_summary

if TYPE_CHECKING:
    from aleph.dtos.flashcards import CitationDTO
    from aleph.services.flashcard_drafting import DraftsView, KeepResultView
    from aleph.services.reviews import (
        CitationView,
        QueueCardView,
        ReviewQueueView,
        ReviewSummaryView,
    )

CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]


def _not_found() -> HTTPException:
    """A `404` rendered through the shared envelope as `not_found`."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


def _conflict(reason: str, message: str) -> HTTPException:
    """A `409` through the shared envelope (the `services/reviews.py::_conflict`
    shape, reused here rather than imported — a router-layer helper, not a
    service one): `code` stays `conflict`, `details.reason` is what a client
    branches on.
    """
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"reason": reason, "message": message},
    )


async def require_flashcards_enabled(user: CurrentUser, session: Session) -> None:
    """Hide the entire flashcards surface unless the `flashcards` flag resolves on.

    Mounted as a **router-level** dependency (see the module docstring), so
    ticket 4's future drafting routes inherit the gate by construction. Off
    (TDD D10's dark-by-default posture) -> `404` for every route in this file,
    before any work — `get_current_user` runs first, so an anonymous request is
    already `401` before the flag is ever consulted, and a disabled flag reads
    as "this route does not exist" rather than "you are not allowed here," the
    same posture `tutor`/`shaping`/`streaks` take.
    """
    flags = await FeatureFlagService(session).resolve_for_user(user)
    if not flags.get(FeatureFlag.FLASHCARDS, False):
        raise _not_found()


async def _owned_lesson_for_drafts(
    lesson_id: UUID, user: CurrentUser, session: Session
) -> Lesson:
    """Resolve `lesson_id` only if it is on a path `user` owns, else `404`.

    The drafting routes' ownership seam (§5.2 #1: "an unowned/unknown lesson is
    `404`, never `403`") — the same `LessonRepository.get_for_user` join
    `routers/v1/lessons.py::get_owned_lesson` uses, respelled locally here
    rather than imported cross-router (each router in this codebase resolves
    its own ownership dependency; `lessons.py` is out of this ticket's edit
    scope regardless).
    """
    lesson = await LessonRepository(session).get_for_user(
        lesson_id=lesson_id, user_id=user.id
    )
    if lesson is None:
        raise _not_found()
    return lesson


OwnedLessonForDrafts = Annotated[Lesson, Depends(_owned_lesson_for_drafts)]


router = APIRouter(
    prefix="/api/v1",
    tags=["flashcards"],
    dependencies=[Depends(require_flashcards_enabled)],
)


def _citation_dto(view: CitationView) -> CitationDTO:
    """D12, on the wire: a `LinkedCitationDTO` carries `lesson_id`; a
    `DegradedCitationDTO` has no such field to carry it in."""
    if view.kind == "linked":
        assert view.lesson_id is not None
        return LinkedCitationDTO(
            lesson_id=view.lesson_id,
            lesson_title=view.lesson_title,
            path_title=view.path_title,
        )
    return DegradedCitationDTO(
        lesson_title=view.lesson_title, path_title=view.path_title
    )


def _queue_card_dto(view: QueueCardView) -> QueueCardDTO:
    return QueueCardDTO(
        card_id=view.card_id,
        front=view.front,
        back=view.back,
        rung=view.rung,
        got_it_interval_days=view.got_it_interval_days,
        path_id=view.path_id,
        source=_citation_dto(view.source),
    )


def _queue_response(view: ReviewQueueView) -> ReviewQueueResponse:
    """Explicit construction (not `model_validate(from_attributes=True)`),
    the one chosen mapping style in this codebase (`_progress_summary_response`
    in `routers/v1/progress.py`)."""
    return ReviewQueueResponse(
        today=view.today,
        total=view.total,
        completed=view.completed,
        scope_path_id=view.scope_path_id,
        other_due_count=view.other_due_count,
        cards=[_queue_card_dto(card) for card in view.cards],
    )


def _summary_response(view: ReviewSummaryView) -> ReviewSummaryResponse:
    return ReviewSummaryResponse(
        today=view.today,
        due_count=view.due_count,
        estimated_minutes=view.estimated_minutes,
        paths=[
            PathDueDTO(path_id=path.path_id, due_count=path.due_count)
            for path in view.paths
        ],
    )


@router.get("/reviews/summary")
async def get_review_summary(
    user: CurrentUser,
    session: Session,
    tz_offset_minutes: TzOffsetMinutes = 0,
) -> ReviewSummaryResponse:
    """Home's card, the app-bar pill, and the per-path chips (D9/§6).

    `tz_offset_minutes` is the client's `getTimezoneOffset()` value verbatim
    (D4), defaulting to `0` (UTC) so an omitted query param still answers
    coherently rather than `422`ing. Out of range is `422 validation_error`
    before this body ever runs.
    """
    view = await load_review_summary(
        session, user_id=user.id, tz_offset_minutes=tz_offset_minutes
    )
    return _summary_response(view)


@router.get("/reviews/queue")
async def get_review_queue(
    user: CurrentUser,
    session: Session,
    tz_offset_minutes: TzOffsetMinutes = 0,
    path_id: UUID | None = None,
) -> ReviewQueueResponse:
    """The day's cards in serve order, plus the counter and `other_due_count` (§5.3/§6).

    `path_id` filters the result **for display only** (PRD §4.3): the
    selection underneath always runs globally, so `total`/`completed` are
    unaffected by this parameter (§5.3's invariant).
    """
    view = await load_review_queue(
        session,
        user_id=user.id,
        tz_offset_minutes=tz_offset_minutes,
        path_id=path_id,
    )
    return _queue_response(view)


@router.post("/reviews")
async def post_review(
    body: GradeCardRequest, user: CurrentUser, session: Session
) -> GradeCardResponse:
    """Grade one card (§5.4/§6): `404` unowned/unknown card, `409 not_due` when
    the card is not today's business, `409 stale_rung` on optimistic-concurrency
    mismatch. Commits once, after the service's one transaction (review-append
    + projection-update) succeeds.
    """
    result = await grade_card(
        session,
        user_id=user.id,
        card_id=body.card_id,
        grade=body.grade,
        rung_before=body.rung_before,
        tz_offset_minutes=body.tz_offset_minutes,
    )
    await session.commit()
    return GradeCardResponse(
        card_id=result.card_id, rung=result.rung, due_on=result.due_on
    )


# --------------------------------------------------------------------------- #
# Drafting (ticket 4, §5.2/§6) — trigger + poll + keep.
# --------------------------------------------------------------------------- #


def _drafts_response(view: DraftsView) -> FlashcardDraftsResponse:
    return FlashcardDraftsResponse(
        state=view.state,
        cards=[
            FlashcardDraftCardDTO(id=card.id, front=card.front, back=card.back)
            for card in view.cards
        ],
    )


def _keep_response(view: KeepResultView) -> KeepFlashcardDraftsResponse:
    return KeepFlashcardDraftsResponse(kept_ids=list(view.kept_ids))


@router.post(
    "/lessons/{lesson_id}/flashcard-drafts",
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_flashcard_drafts(
    lesson: OwnedLessonForDrafts, user: CurrentUser, session: Session
) -> TriggerFlashcardDraftsResponse:
    """Trigger drafting for a completed lesson -> `202`, idempotent (D5/D7/§6).

    Ownership via `OwnedLessonForDrafts` (`404` otherwise, never `403` —
    §5.2 #1). Guards, in order:

    * not `completed_at IS NOT NULL` -> `409 lesson_not_complete` (§5.6): the
      client only ever fires this after a completion, so this is a defensive
      guard against a stray/replayed request, not a path a normal session
      takes.
    * over `flashcard_drafts_per_day` -> `429` through the shared envelope
      (D13), checked *after* the completion guard and *before* the claim, so a
      breach never spends a claim attempt.

    On pass, `FlashcardDraftingService.trigger_draft_run` fires the whole
    claim + run through the registry-bound spawn and this returns immediately
    (D5's trigger + poll, reused verbatim). The claim itself is what makes a
    second `POST` — while `generating`, or once `generated` — a structural
    no-op (D7): this route never re-checks that state itself, and the response
    is the same `202 {id}` either way; the client polls `GET
    .../flashcard-drafts` for the outcome.
    """
    if lesson.completed_at is None:
        raise _conflict(
            "lesson_not_complete",
            "complete this lesson before drafting flashcards from it.",
        )
    limiter = build_daily_rate_limiter(session)
    await limiter.check_flashcard_draft_generation(
        user_id=user.id, is_admin=is_admin(user, settings)
    )
    flashcard_drafting_service.trigger_draft_run(lesson.id)
    return TriggerFlashcardDraftsResponse(id=lesson.id)


@router.get("/lessons/{lesson_id}/flashcard-drafts")
async def get_flashcard_drafts(
    lesson: OwnedLessonForDrafts, session: Session
) -> FlashcardDraftsResponse:
    """Poll: `{state, cards: [{id, front, back}]}` (§6).

    Ownership via `OwnedLessonForDrafts` (`404` otherwise). `state` is
    `"not_started"` when drafting was never triggered for this lesson (no
    `flashcard_draft_runs` row yet, D7's sparse table), `"failed"` is
    retryable by re-`POST`ing the trigger route (renders the existing
    `state-card` retry affordance, never a dead spinner, §5.2 #4), and
    `"generated"` carries every pending draft for this lesson in creation
    order — including on a revisit long after the run resolved (§14: "abandoned
    drafts wait").
    """
    view = await load_flashcard_drafts(session, lesson_id=lesson.id)
    return _drafts_response(view)


@router.post("/lessons/{lesson_id}/flashcard-drafts/keep")
async def post_keep_flashcard_drafts(
    body: KeepFlashcardDraftsRequest,
    lesson: OwnedLessonForDrafts,
    session: Session,
) -> KeepFlashcardDraftsResponse:
    """`{kept_ids: […]}` -> keep those, delete every other pending draft (D6/§6).

    Ownership via `OwnedLessonForDrafts` (`404` otherwise). `kept_ids: []` is
    "Skip — keep none" (D6) — the same request, not a separate route. A
    `kept_id` that is not a pending draft **of this lesson** (another lesson's
    draft, an already-kept card, an unknown id) is a `404` that **mutates
    nothing** (§5.2/§11): `keep_flashcard_drafts` never commits on a short
    count, and this route commits only after it returns successfully, so an
    exception here leaves the session's uncommitted work to be rolled back by
    `get_session`'s context manager rather than persisted.

    Named `post_keep_flashcard_drafts` (not `keep_flashcard_drafts`) to avoid
    shadowing the imported service function of that name in this module's
    namespace.
    """
    result = await keep_flashcard_drafts(
        session,
        lesson_id=lesson.id,
        kept_ids=body.kept_ids,
        tz_offset_minutes=body.tz_offset_minutes,
    )
    await session.commit()
    return _keep_response(result)
