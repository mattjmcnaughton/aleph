"""Flashcards API DTOs: drafting, the review queue, its summary, and grading
(Phase 3 TDD §6).

The wire contract for every route on `routers/v1/flashcards.py`: ticket 5's
three review routes (`GET /reviews/summary`, `GET /reviews/queue`,
`POST /reviews`) plus ticket 4's three drafting routes
(`POST /lessons/{id}/flashcard-drafts`, `GET .../flashcard-drafts`,
`POST .../flashcard-drafts/keep`).

`TzOffsetMinutes` is imported from `dtos/progress.py` rather than redeclared
here (§6) — one constrained `Annotated[int, Field(ge=-900, le=900)]` alias, one
place to be wrong about the client's `getTimezoneOffset()` band. Mapping from
the service's frozen views (`services/reviews.py`) to these models is always
explicit construction in `routers/v1/flashcards.py`, never
`model_validate(from_attributes=True)` — the one chosen mapping style in this
codebase (`_progress_summary_response` in `routers/v1/progress.py`).
"""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from aleph.dtos.progress import TzOffsetMinutes
from aleph.models import FlashcardGrade

__all__ = [
    "CitationDTO",
    "DegradedCitationDTO",
    "FlashcardDraftCardDTO",
    "FlashcardDraftsResponse",
    "GradeCardRequest",
    "GradeCardResponse",
    "KeepFlashcardDraftsRequest",
    "KeepFlashcardDraftsResponse",
    "LinkedCitationDTO",
    "PathDueDTO",
    "QueueCardDTO",
    "ReviewQueueResponse",
    "ReviewSummaryResponse",
    "TriggerFlashcardDraftsResponse",
    "TzOffsetMinutes",
]


class LinkedCitationDTO(BaseModel):
    """A card's source line when the citation still resolves (D12).

    ``kind`` doubles as the discriminator (:data:`CitationDTO`) and as the
    literal wire tag the frontend switches on to render a link.
    """

    kind: Literal["linked"] = "linked"
    lesson_id: UUID
    lesson_title: str
    path_title: str


class DegradedCitationDTO(BaseModel):
    """A card's source line once its lesson is gone or has moved on (D12).

    Deliberately carries **no `lesson_id` field at all** — not a nullable one.
    A discriminated shape is what makes "the degraded case cannot dereference a
    lesson" a property of the schema rather than a convention the frontend has
    to remember (TDD §6: "the frontend renders a link or plain text off `kind`
    and can never dereference a null").
    """

    kind: Literal["degraded"] = "degraded"
    lesson_title: str
    path_title: str


# The discriminated union itself (§6: "`source` is an object, not three flat
# fields"). Pydantic dispatches on `kind` at validation time and — the point of
# choosing this over an untagged union — a `DegradedCitationDTO` instance
# genuinely has no `lesson_id` attribute to accidentally serialize.
CitationDTO = Annotated[
    LinkedCitationDTO | DegradedCitationDTO, Field(discriminator="kind")
]


class QueueCardDTO(BaseModel):
    """One card as the review session shows it (§6's `GET /reviews/queue` example).

    `got_it_interval_days` is what the *Got it* button previews — computed
    server-side from the ladder (`settings.flashcard_ladder`), never
    duplicated as a client-side constant (§8's stated reason: the client must
    not hold a second copy of the ladder).
    """

    card_id: UUID
    front: str
    back: str
    rung: int
    got_it_interval_days: int
    path_id: UUID | None
    source: CitationDTO


class ReviewQueueResponse(BaseModel):
    """`GET /api/v1/reviews/queue` body (§5.3/§6).

    `total`/`completed` are always over the **global** selected set, even when
    `scope_path_id` narrows `cards` to one path (§5.3's invariant — the
    denominator a display filter must never shrink). `other_due_count` is
    non-zero only when `scope_path_id` is set — the end-of-filtered-session
    widen offer (PRD §4.10).
    """

    today: date
    total: int
    completed: int
    scope_path_id: UUID | None
    other_due_count: int
    cards: list[QueueCardDTO]


class PathDueDTO(BaseModel):
    """One path's share of the global selected set.

    Per-path counts sum to the global `due_count` (§5.3): `Review 7` beside
    `10 cards` means seven of today's ten came from that path, never that the
    path itself has seven of its own due.
    """

    path_id: UUID
    due_count: int


class ReviewSummaryResponse(BaseModel):
    """`GET /api/v1/reviews/summary` body (D9/§6): home's card, the app-bar
    pill, and the per-path chips, all from one payload — its own route and its
    own kill switch, deliberately not folded into `/progress/summary` (D9).
    """

    today: date
    due_count: int
    estimated_minutes: int
    paths: list[PathDueDTO]


class GradeCardRequest(BaseModel):
    """`POST /api/v1/reviews` body (§5.4/§6).

    `rung_before` is the optimistic-concurrency token: the client already
    holds it (it rendered `got_it_interval_days` from it), so this adds no
    round trip, and a mismatch is a `409 stale_rung` — a double-tapped button
    or a retried request that actually already succeeded, absorbed as a no-op
    rather than a double promotion.
    """

    card_id: UUID
    grade: FlashcardGrade
    rung_before: int
    tz_offset_minutes: TzOffsetMinutes


class GradeCardResponse(BaseModel):
    """`POST /api/v1/reviews`'s `200` body: the card's new projected state."""

    card_id: UUID
    rung: int
    due_on: date


# --- drafting (ticket 4, §5.2/§6) -----------------------------------------


class TriggerFlashcardDraftsResponse(BaseModel):
    """`POST /api/v1/lessons/{lesson_id}/flashcard-drafts`'s `202` body.

    `id` is the lesson id — the trigger + poll shape verbatim (D5), mirroring
    `GenerateLessonResponse` (`dtos/lessons.py`). The response is identical
    whether this call won the claim or found the run already `generating`/
    `generated` (D7's no-op) — the client cannot and need not distinguish the
    two from this response alone; it polls `GET .../flashcard-drafts` either
    way.
    """

    id: UUID


class FlashcardDraftCardDTO(BaseModel):
    """One drafted card on the poll/keep screen (§6): `{id, front, back}`.

    No `source`/`rung`/`due_on` — a Draft (D6) has none of those yet; they
    exist only once the card is kept.
    """

    id: UUID
    front: str
    back: str


class FlashcardDraftsResponse(BaseModel):
    """`GET /api/v1/lessons/{lesson_id}/flashcard-drafts`'s body (§6): `{state, cards}`.

    `state` is `"not_started"` when drafting was never triggered for this
    lesson (`flashcard_draft_runs` is sparse, D7 — no row yet is a real,
    distinct case), `"generating"`/`"generated"`/`"failed"` otherwise —
    `"failed"` is retryable by re-`POST`ing the trigger route and renders the
    existing `state-card` retry affordance, never a dead spinner (§5.2 #4).
    `cards` is populated only once `state == "generated"`.
    """

    state: Literal["not_started", "generating", "generated", "failed"]
    cards: list[FlashcardDraftCardDTO]


class KeepFlashcardDraftsRequest(BaseModel):
    """`POST .../flashcard-drafts/keep`'s body (§5.2/§6).

    `kept_ids: []` is "Skip — keep none" (D6) — every pending draft of this
    lesson is discarded, nothing kept. `tz_offset_minutes` is the same
    `getTimezoneOffset()`-verbatim field `GradeCardRequest` carries (D4): the
    service is the sole owner of "today" (`due_on = today + ladder[0]`), and
    this request is the one place that arithmetic needs an offset to resolve
    against — TDD §6's wire example does not spell this field out, but the
    day boundary has to come from *somewhere* on this request, and a body
    field is the established shape for a write that needs it.
    """

    kept_ids: list[UUID]
    tz_offset_minutes: TzOffsetMinutes


class KeepFlashcardDraftsResponse(BaseModel):
    """`POST .../flashcard-drafts/keep`'s `200` body: the ids actually kept."""

    kept_ids: list[UUID]
