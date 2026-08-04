"""Flashcards API: drafting, the review queue, its summary, grading, and
(AL-410 / issue #156) the card list / edit / delete surface (Phase 3 TDD
§5.2-6; AL-410 §5).

Ticket 5 shipped `GET /reviews/summary`, `GET /reviews/queue`, `POST /reviews`.
Ticket 4 added the three drafting routes — `POST
/lessons/{id}/flashcard-drafts`, `GET .../flashcard-drafts`, `POST
.../flashcard-drafts/keep`. AL-410 adds a third section, `GET /flashcards`,
`PATCH /flashcards/{card_id}`, `DELETE /flashcards/{card_id}` — to the **same
router**, following `routers/v1/progress.py`'s conventions verbatim:
session-cookie auth (`get_current_user` -> `401` through the shared envelope),
the router-level flag gate (inherited by construction, TDD D10's whole point —
AL-410 adds no gate of its own), and 404-never-403 everywhere ownership is at
stake.

**The flag gate lives here, not in `services/feature_flags.py`.** Every other
flag gate (`require_tutor_enabled`, `require_shaping_enabled`,
`require_streaks_enabled`) is defined in its own router module; this one
follows suit (TDD D10 — it was defined in `services/feature_flags.py` only as
a placeholder until this file existed).

**Read-only except four writes.** `GET /reviews/summary`, `GET
/reviews/queue`, and `GET /flashcards` hold no `session.commit()` — each reads
a derivation of already-committed state (D3/§2). `POST /reviews`, `POST
.../flashcard-drafts/keep`, `PATCH /flashcards/{card_id}`, and `DELETE
/flashcards/{card_id}` are the four writes: each commits once, after its
service function returns, so its transaction (review-append +
projection-update, §5.4; keep-update + discard-delete, §5.2; the text/
`edited_at` update, §3; the soft-delete update, §3) lands atomically. `POST
.../flashcard-drafts` triggers a **background** claim + run
(`FlashcardDraftingService.trigger_draft_run`) and returns `202` with no
session write of its own — the claim commits inside the spawned task, not the
request (§5.2). **No rate limiting on any of AL-410's three routes** — unlike
`POST .../flashcard-drafts`, none of them calls a model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003 - FastAPI resolves query-param annotations.

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import (  # noqa: TC002 - FastAPI resolves annotations.
    AsyncSession,
)

from aleph.authz import is_admin
from aleph.config import settings
from aleph.db import get_session
from aleph.dependencies import get_current_user
from aleph.dtos.flashcards import (
    CardListItemDTO,
    CardListResponse,
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
    UpdateCardRequest,
)
from aleph.dtos.progress import TzOffsetMinutes  # noqa: TC001 - FastAPI resolves it.
from aleph.models import (  # noqa: TC001 - FastAPI resolves annotations.
    Lesson,
    LessonGenerationState,
    User,
)
from aleph.repositories import LessonRepository
from aleph.repositories.flashcards import MAX_CARD_LIST_LIMIT
from aleph.services.feature_flags import FeatureFlag, FeatureFlagService
from aleph.services.flashcard_drafting import (
    flashcard_drafting_service,
    keep_flashcard_drafts,
    load_flashcard_drafts,
)
from aleph.services.rate_limit import build_daily_rate_limiter
from aleph.services.reviews import (
    delete_card,
    edit_card,
    grade_card,
    load_card_list,
    load_review_queue,
    load_review_summary,
)

if TYPE_CHECKING:
    from aleph.dtos.flashcards import CitationDTO
    from aleph.services.flashcard_drafting import DraftsView, KeepResultView
    from aleph.services.reviews import (
        CardListItemView,
        CardListView,
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
    """Trigger drafting for a generated lesson -> `202`, idempotent (D5/D7/§6).

    Ownership via `OwnedLessonForDrafts` (`404` otherwise, never `403` —
    §5.2 #1). Guards, in order:

    * not `generation_state IS GENERATED` -> `409 lesson_not_generated`
      (§5.6). This is now **load-bearing**, not defensive: the client fires
      this route as soon as the learner *opens* a generated, unlocked lesson
      (not on completion — AL-400), so an ungenerated lesson is a real,
      reachable case, not just a stray/replayed request. Without this guard
      an ungenerated lesson would claim a run and burn it on
      `_run_claimed`'s context-missing branch, which emits no event and
      resolves the run `failed` silently.
    * over `flashcard_drafts_per_day` -> `429` through the shared envelope
      (D13), checked *after* the generation guard and *before* the claim, so a
      breach never spends a claim attempt.

    On pass, `FlashcardDraftingService.trigger_draft_run` fires the whole
    claim + run through the registry-bound spawn and this returns immediately
    (D5's trigger + poll, reused verbatim). The claim itself is what makes a
    second `POST` — while `generating`, or once `generated` — a structural
    no-op (D7): this route never re-checks that state itself, and the response
    is the same `202 {id}` either way; the client polls `GET
    .../flashcard-drafts` for the outcome.
    """
    if lesson.generation_state is not LessonGenerationState.GENERATED:
        raise _conflict(
            "lesson_not_generated",
            "this lesson has no content to draft flashcards from yet.",
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


# --------------------------------------------------------------------------- #
# Card management (AL-410 / issue #156, §5) — browse, edit, delete every kept
# card. Same router, same flag gate (D10: one flag stays one flag) — no new
# gate, no new rate limiter (none of the three routes below calls a model).
# --------------------------------------------------------------------------- #


def _card_list_item_dto(view: CardListItemView) -> CardListItemDTO:
    """Explicit construction (not `model_validate(from_attributes=True)`),
    the one chosen mapping style in this codebase, mirroring `_queue_card_dto`.
    """
    return CardListItemDTO(
        id=view.id,
        front=view.front,
        back=view.back,
        rung=view.rung,
        due_on=view.due_on,
        edited_at=view.edited_at,
        source=_citation_dto(view.source),
    )


def _card_list_response(view: CardListView) -> CardListResponse:
    return CardListResponse(
        cards=[_card_list_item_dto(card) for card in view.cards],
        next_cursor=view.next_cursor,
    )


@router.get("/flashcards")
async def get_flashcards(
    user: CurrentUser,
    session: Session,
    limit: int = Query(20, ge=1, le=MAX_CARD_LIST_LIMIT),
    cursor: str | None = None,
    path_id: UUID | None = None,
    q: str | None = None,
) -> CardListResponse:
    """Browse every kept card (§2/§5): most-recently-kept first, cursor-paginated.

    `path_id` and `q` filter **one** list, never two endpoints (§5) — `q` is a
    case-insensitive substring match on either side of the card. A malformed
    `cursor` is a `422` (`load_card_list` raises through the shared envelope),
    never a `500`. `limit` is bounded `[1, MAX_CARD_LIST_LIMIT]` here at the
    query-param layer, imported from the repository rather than a second
    literal `50` — the docstrings on both sides call this one cap enforced
    twice, which is only true so long as there is exactly one `50` for the two
    layers to agree on — and again inside the repository (defense in depth for
    a caller that reaches it directly).
    """
    view = await load_card_list(
        session,
        user_id=user.id,
        limit=limit,
        cursor=cursor,
        path_id=path_id,
        query=q,
    )
    return _card_list_response(view)


@router.patch("/flashcards/{card_id}")
async def patch_flashcard(
    card_id: UUID, body: UpdateCardRequest, user: CurrentUser, session: Session
) -> CardListItemDTO:
    """Edit a kept card's text (§3/§5): `404` unowned/unknown/draft/deleted —
    `UpdateCardRequest` itself already rejects an empty/over-cap/identical-sides
    body as `422`, before this body ever runs. Never touches `rung`/`due_on` —
    fixing wording does not reset what the learner knows. Commits once, after
    the service returns.
    """
    view = await edit_card(
        session, user_id=user.id, card_id=card_id, front=body.front, back=body.back
    )
    await session.commit()
    return _card_list_item_dto(view)


@router.delete("/flashcards/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_flashcard(card_id: UUID, user: CurrentUser, session: Session) -> None:
    """Soft-delete a kept card (§1/§3/§5): `404` unowned/unknown/already-deleted
    — a double-tapped delete is an honest `404`, not a silent second success.
    Commits once, after the service returns.
    """
    await delete_card(session, user_id=user.id, card_id=card_id)
    await session.commit()
