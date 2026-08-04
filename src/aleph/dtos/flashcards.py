"""Flashcards API DTOs: drafting, the review queue, its summary, grading, and
(AL-410/issue #156) the card list / edit / delete surface (Phase 3 TDD §6;
AL-410 §4).

The wire contract for every route on `routers/v1/flashcards.py`: ticket 5's
three review routes (`GET /reviews/summary`, `GET /reviews/queue`,
`POST /reviews`), ticket 4's three drafting routes
(`POST /lessons/{id}/flashcard-drafts`, `GET .../flashcard-drafts`,
`POST .../flashcard-drafts/keep`), and AL-410's three card-management routes
(`GET /flashcards`, `PATCH /flashcards/{id}`, `DELETE /flashcards/{id}`).

`TzOffsetMinutes` is imported from `dtos/progress.py` rather than redeclared
here (§6) — one constrained `Annotated[int, Field(ge=-900, le=900)]` alias, one
place to be wrong about the client's `getTimezoneOffset()` band. Mapping from
the service's frozen views (`services/reviews.py`) to these models is always
explicit construction in `routers/v1/flashcards.py`, never
`model_validate(from_attributes=True)` — the one chosen mapping style in this
codebase (`_progress_summary_response` in `routers/v1/progress.py`).
"""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, model_validator

from aleph.agents.flashcard import (
    FlashcardCaps,
    is_non_empty,
    sides_differ,
    within_word_cap,
)
from aleph.dtos.progress import TzOffsetMinutes
from aleph.models import FlashcardGrade

__all__ = [
    "CardListItemDTO",
    "CardListResponse",
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
    "UpdateCardRequest",
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


# --- card management (AL-410 / issue #156, §2-5) ---------------------------


class CardListItemDTO(BaseModel):
    """One kept card on `GET /api/v1/flashcards` (§4/§5's browse surface).

    `rung` ships on the wire — the ticket's own field list requires it — but
    the frontend must not render it (see the plan's two already-settled
    product calls): *rung* is scheduler vocabulary `docs/CONTEXT.md` never
    gives the learner, and a row shows only its `due_on` (`Due in 3 days` /
    `Due today` / `Due yesterday`). `edited_at` is `None` for a card whose
    text has never been learner-edited since it was kept.
    """

    id: UUID
    front: str
    back: str
    rung: int
    due_on: date
    edited_at: datetime | None
    source: CitationDTO


class CardListResponse(BaseModel):
    """`GET /api/v1/flashcards`'s body (§4/§5): one page plus its opaque cursor.

    `next_cursor` is `None` once the last page is reached — the client's
    "Load more" affordance simply disappears rather than firing a request
    that would come back empty.
    """

    cards: list[CardListItemDTO]
    next_cursor: str | None


# Character-level backstop for `UpdateCardRequest.front`/`back` (AL-410 review
# finding 1). `within_word_cap` (below) bounds *word* count via
# `str.split()` — so a single whitespace-free token of any length (one giant
# "word": a pasted URL, a base64 blob, a script kiddie's literal 500,000
# characters) sails through every shape predicate untouched, and
# `Flashcard.front`/`back` are unbounded `Text` columns underneath. This is
# the one learner-writable free-text field in the codebase that had no length
# bound at all — contrast `dtos/paths.py`'s `TopicStr`/`GuidanceStr`/
# `PathTitleStr` and `dtos/tutor.py`'s `TutorMessageStr`, all
# `Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
# max_length=N)]`. Followed exactly here, down to the shape.
#
# The two bounds are picked **well above** the word caps (25 front words / 60
# back words, `FlashcardCaps()`'s defaults) on purpose: this is a hard
# backstop against a pathological payload reaching the database, not a
# second, character-shaped word cap competing with `within_word_cap` below —
# a card that actually obeys the word cap never comes remotely close to
# either bound, so the two rules never disagree about a legitimate edit.
# `min_length=1`/`strip_whitespace=True` overlap the model validator's own
# `is_non_empty` check below — deliberately belt-and-braces (the same posture
# `append_review_and_project`'s scoped `UPDATE` takes), not a redundancy to
# clean up: the field constraint is what actually stops an oversized/blank
# payload from reaching `_check_shape` at all, and `is_non_empty` is what
# still catches internal-whitespace-only text that formatting alone would not.
CARD_FRONT_MAX_CHARS = 1000
CARD_BACK_MAX_CHARS = 2000

CardFrontStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=CARD_FRONT_MAX_CHARS
    ),
]
CardBackStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=CARD_BACK_MAX_CHARS
    ),
]


class UpdateCardRequest(BaseModel):
    """`PATCH /api/v1/flashcards/{card_id}`'s body (§4/§5).

    Reuses the flashcard **agent's own** shape predicates —
    `aleph.agents.flashcard.is_non_empty` / `within_word_cap` / `sides_differ`
    — against `FlashcardCaps()`'s defaults (25 front words / 60 back words),
    the same band `services/flashcard_drafting.py` leaves the caps at for an
    agent-drafted card. A learner-written edit is held to the same shape an
    agent-written card is; a violation is a `422` through the shared
    envelope, before the router or the repository ever sees it.

    `front`/`back` are also bounded at the **character** level
    (`CardFrontStr`/`CardBackStr`, 1000/2000 chars) — a backstop against a
    single whitespace-free token the word cap cannot see (see the two types'
    own comment, just above). This is the one learner-writable free-text
    field in the codebase that sits at the trust boundary `edited_at` was
    added to mark, and until this bound existed a `PATCH` with a
    500,000-character `front` returned `200` and persisted the whole thing.

    `dtos -> agents` is a safe import direction: `tests/unit/test_agents_layering.py`
    only forbids the reverse (an agent module importing an application
    layer). This module is a DTO, not an agent, and the predicates it reuses
    are plain, config-free functions with no bound model — importing them
    costs nothing `agents/flashcard.py` itself does not already pay, and is
    cheaper than re-deriving the same four checks a second time and letting
    them drift.
    """

    front: CardFrontStr
    back: CardBackStr

    @model_validator(mode="after")
    def _check_shape(self) -> "UpdateCardRequest":
        caps = FlashcardCaps()
        if not is_non_empty(self.front):
            raise ValueError("front must not be empty.")
        if not is_non_empty(self.back):
            raise ValueError("back must not be empty.")
        if not within_word_cap(self.front, maximum=caps.front_words_max):
            raise ValueError(f"front must be at most {caps.front_words_max} words.")
        if not within_word_cap(self.back, maximum=caps.back_words_max):
            raise ValueError(f"back must be at most {caps.back_words_max} words.")
        if not sides_differ(self.front, self.back):
            raise ValueError("front and back must differ.")
        return self
