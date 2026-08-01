"""Shaping API request/response DTOs (AL-320, Phase 2B TDD §6).

The wire contract for the shaping surface: the conversation read, the streamed
send, and the one new stream event a shaping reply can carry — ``proposal``.
Phase 2A's tutor contract (``dtos/tutor.py``) is **reused, not respelled**:
``TutorMessageStr`` bounds the learner's message, and ``delta`` / ``done`` /
``error`` ride the exact shapes the in-lesson rail already parses (§5.4 is
"Phase 2 §5.4 verbatim, plus one named event"). Only what genuinely differs
lives here.

**What differs, and only this.**

* **No lesson.** A shaping turn is about the path as a whole (PRD §5.1), so
  :class:`ShapingMessageDTO` carries no ``lesson_id`` and no ``lesson_title``.
  The columns exist and are ``NULL`` on these rows (migration ``0006``); a wire
  field that is always ``null`` would invite a client to render an empty lesson
  divider for a turn that was never in a lesson.
* **A Proposal may ride on a tutor message**, exactly as a Tutor check rides on
  one in the in-lesson thread — same position in the shape, same
  non-null-only-on-tutor-rows rule.

**Two proposal shapes, and the split is the wire contract.**

:class:`ProposalPayloadDTO` is the **bare validated payload** —
``{operations, summary}``, TDD §4's fixed shape and nothing else. It is what
the ``proposal`` SSE event carries and what is persisted on the tutor message
row, and those two being the same object is deliberate: the card the rail draws
mid-stream and the card it re-renders from a later thread read are then the same
card by construction.

:class:`ProposalDTO` is that payload plus ``resolution`` — *pending*, *applied*,
*undone* or *superseded* — for the conversation read. Resolution is **derived,
never stored** (D3), by
:func:`~aleph.services.tutor_context.derive_proposal_resolutions`, the same
function the shaper's carried history uses; computing it twice is how the card
and the model start disagreeing about whether an edit already landed. It is
absent from the stream event because a proposal that has just been made is
pending by definition, and a field whose only possible value is a constant is
noise on the wire.

**AL-321 adds the write half**: :class:`ShapingConflictReason` (the coded ``409``
a card renders when Apply or Undo refuses), :class:`ChangeDTO` and
:class:`ChangeHistoryResponse` (the read-only **Change history**), and
:class:`ApplyProposalResponse` (the committed Change plus the refreshed path, so
ghost rows become real rows in one round trip). Nothing in the conversation
contract above changed to accommodate them — see :class:`ProposalDTO` for why the
card carries no ``change_id``.

**The operations are the agent's own models**, imported from
``agents/shaper.py`` rather than restated: the closed two-shape vocabulary (D1)
is validated by predicates that live there, and a second declaration of the same
payload here is precisely how a DTO and a validator drift apart. Their
docstrings and field constraints therefore *are* the wire documentation.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - pydantic resolves annotations.
from enum import StrEnum
from uuid import UUID  # noqa: TC003 - pydantic resolves annotations.

from pydantic import BaseModel, ConfigDict

from aleph.agents.shaper import ShapingOperation  # noqa: TC001 - pydantic needs it
from aleph.dtos.paths import PathDetailResponse  # noqa: TC001 - pydantic needs it
from aleph.dtos.tutor import TutorMessageStr  # noqa: TC001 - pydantic needs it
from aleph.models import (
    MessageRole,
    MessageSource,
    PathChangeKind,
    PathChangeStatus,
)


class ProposalResolutionDTO(StrEnum):
    """Where a Proposal stands (CONTEXT.md; TDD §4 — derived, never stored).

    A ``StrEnum`` member *is* its wire value, and the values are exactly
    ``agents/shaper.py``'s ``ProposalResolution`` literals: the derivation
    returns those strings and this enum is how they reach the client's card
    state machine (TDD §8) with one spelling.
    """

    #: No change row references it and nothing has superseded it — the card
    #: still offers **Apply**.
    PENDING = "pending"
    #: A live ``path_changes`` row references it: the edit landed.
    APPLIED = "applied"
    #: Its change was undone; the path is back where it was.
    UNDONE = "undone"
    #: A later Proposal was applied first and this one no longer validates
    #: against live path state — the card explains why and offers "ask again".
    SUPERSEDED = "superseded"


class ProposalPayloadDTO(BaseModel):
    """The validated **Proposal** payload: what the edit *is* (TDD §4).

    ``{operations, summary}`` — the closed vocabulary of **Additions** and
    **Revisions** (D1) plus the plain-language line stating what the payload
    does. This is the ``proposal`` SSE event's data, verbatim, and it is what is
    stored on the tutor message row; nothing else is added on either rail.

    Persisting a Proposal is not applying one. Nothing in this payload changes a
    path until the learner taps **Apply** (D5) — consent is structural, and this
    object is the statement of the offer, not of a change.
    """

    operations: list[ShapingOperation]
    summary: str


class ProposalDTO(ProposalPayloadDTO):
    """A stored Proposal as the conversation read reports it (§6).

    The payload plus its **derived** ``resolution`` (D3). The card renders its
    state from this one field, so it is computed by the shared derivation rather
    than by anything local to this surface.

    **There is deliberately no ``change_id``** (AL-321). The card's states are
    §8's — pending / applying / applied ("view in path") / stale / undone — and
    none of them needs one: **Apply** answers with the whole
    :class:`ChangeDTO`, so a card that just applied holds the id it needs, and
    **Undo** is reached from the change-history sheet, which carries every id by
    definition. Adding it here would mean a nullable field that is ``None`` for
    the entire life of the ordinary case, plus a second derivation to keep in
    step with ``resolution`` — the exact drift D3 avoided by not storing a status
    column. If a card ever grows an undo affordance of its own, the additive move
    is to put the id in the same derivation that already computes the state, not
    beside it.
    """

    resolution: ProposalResolutionDTO


class ShapingMessageDTO(BaseModel):
    """One message in the **Shaping conversation** — learner or tutor (§6).

    No lesson fields, deliberately (module docstring): shaping is path-level.
    ``proposal`` is non-null only on a tutor message that made one. The learner
    row's ``source`` stays off the wire for 2A's reason — it is the §7 entry-mix
    datum, carried by the product events, and the rail renders a learner bubble
    the same either way.
    """

    id: UUID
    role: MessageRole
    content: str
    proposal: ProposalDTO | None
    created_at: datetime


class ShapingConversationResponse(BaseModel):
    """``GET /api/v1/paths/{id}/shaping/conversation`` body — the whole thread.

    Object-wrapped (never a bare array) like every other list payload. Messages
    are in ``position`` order, oldest first.

    A path with no shaping conversation yet answers ``200`` with an empty list,
    not ``404``: the row is created lazily on the first completed turn (§5.5),
    so an untouched path and a cleared one read identically — which is exactly
    what "new conversation" should leave behind. **Unpaginated this phase**, the
    same accepted risk as the in-lesson thread (TDD §14).
    """

    messages: list[ShapingMessageDTO]


class ShapingConflictReason(StrEnum):
    """Why a ``409`` refused an **Apply** or an **Undo** (AL-321, TDD §5.8).

    Every one of these is an ordinary ``409 conflict`` in the shared envelope;
    this is the ``details.reason`` beside it, and it exists because §5.8 makes
    the stale path **first-class UX** rather than an error corner: the card has
    to say *which* thing changed and offer the matching affordance ("ask again",
    "retry in a moment", "this is permanent now"). A single opaque
    ``conflict`` would leave the rail guessing from prose.

    They partition into five groups the card treats differently:

    * **Nothing to do** — ``already_applied`` / ``already_undone`` /
      ``not_applied``. Idempotent-friendly: the learner (or a double tap) asked
      for a state the path is already in.
    * **Stale, so ask again** — ``path_cap_reached``, ``insert_position_taken``,
      ``revision_target_engaged``, ``title_conflict``, ``positions_shifted``,
      ``invalid_proposal``. The Proposal was valid when drafted and is not any
      more (D5); re-asking is the way forward, retrying is not.
    * **Retry in a moment** — ``target_generating``. A prefetch holds the claim;
      nothing is wrong and the same tap will work shortly.
    * **Undo something else first** — ``not_latest``. Nothing is wrong with this
      Change either; it is simply not the one on top of the stack.
    * **Closed for good** — ``engaged``. The learner met the content, so the
      Change is permanent history (PRD §5.5) and the UI says so plainly rather
      than hiding the button.
    """

    #: A live change row already references this Proposal.
    ALREADY_APPLIED = "already_applied"
    #: This Proposal was applied and undone; it is spent (ask again to redo it).
    ALREADY_UNDONE = "already_undone"
    #: Undo of a Change that is not ``applied`` (already undone).
    NOT_APPLIED = "not_applied"
    #: Undo of a live Change a *later* live Change was applied on top of. Undo
    #: is last-in-first-out: reverse the later one first.
    NOT_LATEST = "not_latest"
    #: The path no longer has room for the additions (``MAX_LESSONS_PER_PATH``).
    PATH_CAP_REACHED = "path_cap_reached"
    #: The insertion point is now before the learner's first non-engaged
    #: position, or past the end of the path.
    INSERT_POSITION_TAKEN = "insert_position_taken"
    #: A Revision names a lesson the learner has started since — or one that is
    #: no longer on this path.
    REVISION_TARGET_ENGAGED = "revision_target_engaged"
    #: A proposed title now collides with one already on the path.
    TITLE_CONFLICT = "title_conflict"
    #: An earlier Change moved the slot this Proposal's positions named.
    POSITIONS_SHIFTED = "positions_shifted"
    #: The payload no longer satisfies the shared predicates for some other
    #: reason (the catch-all, so the set stays closed as the rulebook grows).
    INVALID_PROPOSAL = "invalid_proposal"
    #: A revision target is generating right now — retryable in a moment.
    TARGET_GENERATING = "target_generating"
    #: The learner has engaged with what the Change created or revised, so undo
    #: is closed (D2 — the server is the enforcer, not the disabled button).
    ENGAGED = "engaged"


class ChangeDTO(BaseModel):
    """One applied **Change** as the history sheet reports it (§6).

    Plain-language summary, the edit shape(s), the status, the timestamps —
    TDD §6's list and nothing more. It is a **record, not a second edit
    surface** (PRD §5.5), so there is no payload here: the operations already
    rendered as a card in the thread, and the history is what happened, in the
    learner's own summary.

    ``kinds`` is plural because one Apply may carry both shapes: a Proposal's
    ``operations`` are a list of Additions *and* Revisions (D1), and they land
    as one Change because a Change is the unit of Apply **and** of Undo
    (CONTEXT.md) — undoing half of what the learner consented to as one edit
    would leave the path in a shape nobody proposed. The row's own ``kind``
    column names the change's dominant shape; this field is derived from the
    payload and is what the sheet renders.

    ``undone_at`` is ``None`` for a live Change. Whether undo is still *open* is
    deliberately **not** here: it is the D2 engagement re-check, run at undo
    time, because the learner can engage at any moment and a flag computed at
    read time would be stale before it was rendered. The ``409 engaged`` is the
    enforcer; the client's own knowledge of the path is the convenience.
    """

    id: UUID
    summary: str
    kinds: list[PathChangeKind]
    status: PathChangeStatus
    applied_at: datetime
    undone_at: datetime | None


class ChangeHistoryResponse(BaseModel):
    """``GET /api/v1/paths/{id}/changes`` body — the whole **Change history**.

    Object-wrapped (never a bare array) like every other list payload, and
    **newest first**: the history reads in the order the learner made it happen.
    Undone Changes are included — undo is a status, not a delete, and the
    history is the record of what happened (CONTEXT.md).

    It survives "new conversation" (PRD §5.8): the rows hang off the path, not
    off the thread, so this list is answered identically before and after a
    learner clears their shaping conversation.
    """

    changes: list[ChangeDTO]


class ApplyProposalResponse(BaseModel):
    """``POST /api/v1/messages/{id}/apply-proposal`` body (§5.6 step 4).

    The Change that was just committed **and the refreshed path**, in one round
    trip, so the rail can swap its **ghost rows** for real ones without a second
    request and without guessing what landed. ``path`` is exactly what
    ``GET /paths/{id}`` returns — the same query Phase 1's polling populates
    (TDD §8) — so the client drops it straight into the cache it already has.

    A Change is *applied* when the structure lands, not when generation finishes
    (PRD §5.7): the added rows in ``path`` are ``ungenerated`` and a revised one
    has gone back to ``ungenerated``, and Phase 1's untouched pipeline takes it
    from there.
    """

    change: ChangeDTO
    path: PathDetailResponse


class SendShapingMessageRequest(BaseModel):
    """``POST /api/v1/paths/{id}/shaping/conversation/messages`` body (§5.4).

    ``content`` reuses 2A's :data:`~aleph.dtos.tutor.TutorMessageStr` — stripped,
    non-empty, ≤ 2000 characters, rejected as a ``422`` *before* the stream opens
    — because it is the same thing: one learner message typed into one rail.

    There is no ``lesson_id``: a shaping turn is about the path as a whole
    (PRD §5.1). ``source`` records how the learner entered it (the four §5.3
    suggestions send as ``suggestion``), the §7 entry-mix datum.

    ``model`` is the **admin** per-message model override (TDD §5.3/D10),
    enforced exactly as 2A's: ``403`` for a non-admin (checked before the
    allowlist), ``422`` off ``MODEL_ALLOWLIST``, resolved per request and
    **persisted nowhere**. It binds the shaper slot (AL-301 put ``MODEL_SHAPER``
    behind the same allowlist), so an A/B of proposal quality costs no deploy.
    """

    # ``model`` starts with the ``model_`` prefix pydantic protects by default;
    # the picker's wire contract fixes the name (parity with ``MODEL_SHAPER``
    # and with 2A's ``SendMessageRequest``), so opt out of the protected
    # namespace rather than rename.
    model_config = ConfigDict(protected_namespaces=())

    content: TutorMessageStr
    source: MessageSource = MessageSource.TYPED
    model: str | None = None
