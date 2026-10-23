"""Learning Tutor state API. Generation admission is deliberately not exposed yet."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from aleph.domains.learning_recommendations import (
    Decision,
    Kind,
    RecommendationStatus,
)

Revision = Annotated[int, Field(ge=0)]


class EvidenceReference(BaseModel):
    """Small factual provenance, never source bodies or context snapshots."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["path", "beat", "brief", "focus", "message"]
    id: UUID | None = None
    completed_lessons: int | None = Field(default=None, ge=0)
    published_briefs: int | None = Field(default=None, ge=0)
    read_briefs: int | None = Field(default=None, ge=0)
    observed_at: datetime | None = None

    @model_validator(mode="after")
    def source_identity(self) -> "EvidenceReference":
        if (self.kind in {"path", "beat", "brief"}) != (self.id is not None):
            raise ValueError("source evidence requires an ID; focus/message must not")
        return self


class RecommendationDraft(BaseModel):
    """Validated publication payload; LT-2 resolves evidence keys to references."""

    model_config = ConfigDict(extra="forbid")
    kind: Kind
    topic: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ]
    level: Literal["new_to_it", "some_experience", "work_in_it"]
    guidance: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
        ]
        | None
    ) = None
    description: str = Field(min_length=1, max_length=300)
    rationale: str = Field(min_length=1, max_length=600)
    evidence: list[EvidenceReference] = Field(min_length=1, max_length=3)


class FocusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    focus: Annotated[str, StringConstraints(max_length=2000)] | None
    revision: Revision


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Decision
    revision: Revision


class RecommendationDTO(BaseModel):
    id: UUID
    kind: Kind
    status: RecommendationStatus
    topic: str | None
    level: str | None
    guidance: str | None
    description: str | None
    rationale: str | None
    evidence: list[EvidenceReference] | None
    created_at: datetime
    accepted_at: datetime | None
    target_id: UUID | None
    target_available: bool


class OperationDTO(BaseModel):
    id: UUID
    epoch: int
    kind: Literal["turn", "refresh"]
    status: Literal["running", "succeeded", "failed"]
    error_code: str | None
    input: dict | None
    output: dict | None
    started_at: datetime
    finished_at: datetime | None
    cleared: bool


class HistoryPage(BaseModel):
    items: list[OperationDTO]
    before: UUID | None = None


class RecommendationPage(BaseModel):
    items: list[RecommendationDTO]
    before: UUID | None = None


class LearningTutorStateDTO(BaseModel):
    focus: str | None
    revision: int
    focus_version: int
    epoch: int
    recommendations: list[RecommendationDTO]
    latest_refresh: OperationDTO | None
    current_operation_id: UUID | None
    history: HistoryPage
