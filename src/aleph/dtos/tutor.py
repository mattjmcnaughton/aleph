"""Tutor API request/response DTOs (AL-221, Phase 2 TDD §6).

The wire contract for the tutor surface — the conversation read, the
Tutor-check answer, and (AL-220) the streamed send. They live here, ahead of the
send endpoint, so the turn service and the rail (AL-230/231) share one
definition instead of two that drift.

Enums reuse the model ``StrEnum``s (``MessageRole``, ``MessageSource``) so the
frontend's ``lib/api.ts`` fixes one set of strings — same word, same meaning,
prose to schema (CONTEXT.md). All addressing is by UUID; DTOs stay separate from
the ORM models (CLAUDE.md).

**A deliberate asymmetry with the Quick check** (TDD §6). ``QuickCheckDTO``
hides ``correct_index``/``explanation`` until an Attempt exists — that is Phase
1's answer-hiding invariant (W6). :class:`TutorCheckDTO` **carries** both on
delivery, by design: a Tutor check is the tutor's own **non-scoring** question,
its feedback is immediate and client-side, and nothing downstream grades it. The
invariant being protected is the *Quick check's* answer, and that protection is
behavioural (TDD D7 — prompt rule, deterministic pre-filter, W13), not a
property of this DTO.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from aleph.models import MessageRole, MessageSource

# One learner message (CONTEXT.md: *Turn*). Stripped of surrounding whitespace,
# required non-empty, and bounded at 2000 characters (TDD §5.5) so a pathological
# payload cannot reach the model or the DB Text column unchecked. A violation is
# a ``422`` through the shared validation envelope (``app.py``), never a 500 —
# and it is rejected *before* the stream opens, so it is an ordinary JSON error.
TutorMessageStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]


class TutorCheckDTO(BaseModel):
    """A Tutor check as it rides on a tutor message (PRD §5.5).

    The full payload the ``pose_tutor_check`` tool produced, plus
    ``answered_index`` — the option the learner picked, ``None`` until the
    check-answer endpoint records one. Carrying ``correct_index`` and
    ``explanation`` is deliberate (see the module docstring): the card reveals
    feedback client-side the instant an option is tapped, and re-opening the
    thread has to render that same revealed state without a second round trip.

    A Tutor check is non-scoring and outside progress: answering one creates no
    Attempt and changes no lesson, progression, or §7 Attempt-derived metric
    (W12).
    """

    stem: str
    options: list[str]
    correct_index: int
    explanation: str
    answered_index: int | None = None


class MessageDTO(BaseModel):
    """One message in the thread — learner or tutor (§6).

    ``lesson_id`` + ``lesson_title`` are **the lesson the message was asked in**
    (PRD §5.8), which is what lets a revisited thread show where each turn
    happened and what 2B renders its lesson dividers from. The conversation is
    per *path*, not per lesson, so these vary down a single thread.

    ``tutor_check`` is non-null only on a tutor message that posed one. The
    learner-row ``source`` (typed vs suggestion) is deliberately **not** on the
    wire: it is the §7 entry-mix datum, carried by the product events, and the
    rail renders a learner bubble the same either way.
    """

    id: UUID
    role: MessageRole
    content: str
    lesson_id: UUID
    lesson_title: str
    tutor_check: TutorCheckDTO | None
    created_at: datetime


class ConversationResponse(BaseModel):
    """``GET /api/v1/paths/{id}/conversation`` body — the whole thread.

    Object-wrapped (never a bare array) like every other list payload, so the
    response can gain fields without a breaking change. Messages are in
    ``position`` order, oldest first.

    A path with no conversation yet answers ``200`` with an empty list, not
    ``404``: the row is created lazily on the first completed turn (TDD §4), so
    "no thread yet" is the opening state of every path rather than a missing
    resource. **Unpaginated this phase** — an accepted risk (TDD §14): a long
    thread is a whole-payload read.
    """

    messages: list[MessageDTO]


class TutorCheckAnswerRequest(BaseModel):
    """``POST /api/v1/messages/{id}/tutor-check-answer`` body.

    ``selected_index`` is the 0-based index into the check's ``options``. Unlike
    the Quick check's :class:`~aleph.dtos.lessons.AttemptRequest` — where an
    out-of-range index simply grades ``incorrect`` — this one is bounded at the
    route (``422``): nothing grades a Tutor check, so the index is *only* ever
    used to index ``options`` when re-rendering the revealed card. An
    unindexable value would be stored happily and break that render later.
    """

    selected_index: int = Field(ge=0)


class SendMessageRequest(BaseModel):
    """``POST /api/v1/paths/{id}/conversation/messages`` body (AL-220, §5.4).

    Defined here with the rest of the tutor contract so the rail and the turn
    service share one shape; the endpoint itself lands with AL-220.

    ``lesson_id`` is the lesson the question is asked in — the scope the tutor
    grounds on this phase, and the tag persisted on both rows of the turn.
    ``source`` records how the learner entered it (a suggestion button sends as
    if typed, with ``source=suggestion``), the §7 entry-mix datum.

    ``model`` is the **admin** per-message model override (TDD §5.3): a bare
    OpenRouter id from the ``MODEL_ALLOWLIST`` the session endpoint exposes to
    admins, enforced server-side exactly like Phase 1's picker (``403``
    non-admin, ``422`` off-allowlist) and resolved per request, **persisted
    nowhere** — a tutor reply is request-scoped, so there is nothing to resume
    and no column to add. The field being present is never itself a grant.
    """

    # ``model`` starts with the ``model_`` prefix pydantic protects by default;
    # the picker wire contract fixes the name (parity with the ``MODEL_TUTOR``
    # config slot and Phase 1's ``model_outline``/``model_lesson``), so opt out
    # of the protected namespace rather than rename.
    model_config = ConfigDict(protected_namespaces=())

    lesson_id: UUID
    content: TutorMessageStr
    source: MessageSource = MessageSource.TYPED
    model: str | None = None
