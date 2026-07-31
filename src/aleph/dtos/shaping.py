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
from aleph.dtos.tutor import TutorMessageStr  # noqa: TC001 - pydantic needs it
from aleph.models import MessageRole, MessageSource


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
