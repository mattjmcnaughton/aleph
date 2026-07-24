"""Paths API request/response DTOs (AL-050, TDD §6).

The wire contract for ``/api/v1/paths`` that the frontend (AL-061/062/064)
consumes. Every enum here is a ``StrEnum`` whose *values* are the exact strings
the frontend's ``lib/api.ts`` fixes (``PathStatus``, ``LessonGenerationState``,
and the derived ``UnlockState``) — same word, same meaning, prose to schema
(CONTEXT.md). All addressing is by UUID (TDD §6). DTOs are always separate from
the ORM models (CLAUDE.md).

The two orthogonal lesson axes (CONTEXT.md) both surface on a lesson DTO:
``generation_state`` (the stored, effective system axis — a stale ``generating``
reads as ``failed``, §5.4) and ``unlock_state`` (the derived learner axis —
``locked``/``available``/``complete``, from ``domains/progression``).
"""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

from aleph.domains.progression import UnlockState
from aleph.models import LessonGenerationState, Level, PathStatus

# The learner's free-text topic (CONTEXT.md: *Topic*). Stripped of surrounding
# whitespace, required non-empty, and bounded so a pathological payload cannot
# reach the model or the DB Text column unchecked. A violation is a ``422``
# through the shared validation envelope (``app.py``), never a 500.
TopicStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]


class CreatePathRequest(BaseModel):
    """``POST /api/v1/paths`` body: the topic + onboarding level (W1).

    ``model_outline``/``model_lesson`` are the **admin** model-picker overrides
    (AL-052, TDD §5.3/D14): optional bare OpenRouter ids the frontend picker
    (AL-065) sends, one per generation slot, selected from the ``MODEL_ALLOWLIST``
    the session endpoint exposes to admins. Omitted (the common case) means the
    configured slot default. They are *authorization-* and *allowlist-*enforced
    server-side at the route (403 non-admin, 422 off-allowlist) — the field
    merely carries the request; the wire field being present is never itself a
    grant.
    """

    # ``model_outline``/``model_lesson`` start with the ``model_`` prefix pydantic
    # protects by default; the picker wire contract fixes these names (parity with
    # the ``MODEL_OUTLINE``/``MODEL_LESSON`` config slots), so opt out of the
    # protected namespace rather than rename.
    model_config = ConfigDict(protected_namespaces=())

    topic: TopicStr
    level: Level
    model_outline: str | None = None
    model_lesson: str | None = None


class CreatePathResponse(BaseModel):
    """``202`` body for create and retry — the path's UUID to poll (§5.4)."""

    id: UUID


class PathProgressDTO(BaseModel):
    """Per-path lesson roll-up (from ``LessonRepository.progress_summaries``).

    ``generated_lessons``/``completed_lessons`` over ``total_lessons`` drive the
    switcher's progress summary and the path-view progress. Counts are over the
    *effective* generation state (stale ``generating`` → ``failed``, §5.4).
    """

    total_lessons: int
    generated_lessons: int
    completed_lessons: int


class PathSummaryDTO(BaseModel):
    """One row of the "Your paths" switcher (``GET /api/v1/paths``, §6)."""

    id: UUID
    topic: str
    level: Level
    status: PathStatus
    progress: PathProgressDTO


class PathListResponse(BaseModel):
    """``GET /api/v1/paths`` body: the learner's paths, newest first.

    Wrapped in an object (never a bare top-level array) so the payload can grow
    fields without a breaking shape change.
    """

    paths: list[PathSummaryDTO]


class LessonSummaryDTO(BaseModel):
    """A lesson's slot in the path outline (no content — that is the lesson API).

    ``generation_state`` is the effective system axis; ``unlock_state`` is the
    derived learner axis (CONTEXT.md's two orthogonal axes).
    """

    id: UUID
    title: str
    position_in_path: int
    position_in_unit: int
    generation_state: LessonGenerationState
    unlock_state: UnlockState


class UnitDTO(BaseModel):
    """A unit and its ordered lessons in the path outline (§6)."""

    id: UUID
    title: str
    summary: str
    position: int
    lessons: list[LessonSummaryDTO]


class PathDetailResponse(BaseModel):
    """``GET /api/v1/paths/{id}`` body — the poll target (§5.4/§6).

    ``status`` is the **effective** path status (a stale ``generating`` outline
    reads as ``failed`` so the client shows retry, not a dead spinner).
    ``refusal_message`` is populated **only** when ``status == refused`` (W7) — a
    ``failed`` path carries ``null`` here, keeping refusal and failure distinct.
    """

    id: UUID
    topic: str
    level: Level
    status: PathStatus
    refusal_message: str | None
    progress: PathProgressDTO
    units: list[UnitDTO]
