"""Account-owned Learning Tutor state and durable operation/acceptance receipts.

Composite FKs enforce owner identity. Separate single-column SET NULL FKs remove
deleted references without nulling the non-null account key (PostgreSQL checks
the composite NO ACTION constraint after the referential action).
"""

from __future__ import annotations

import uuid  # noqa: TC003
from datetime import datetime  # noqa: TC003

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aleph.db import Base
from aleph.models.base import UUIDAuditMixin


class LearningTutorState(Base):
    __tablename__ = "learning_tutor_states"
    __table_args__ = (
        CheckConstraint(
            "revision >= 0 AND focus_version >= 0 AND epoch >= 0",
            name="ck_lt_state_versions",
        ),
        ForeignKeyConstraint(
            ["user_id", "last_refresh_operation_id"],
            ["learning_tutor_operations.user_id", "learning_tutor_operations.id"],
            name="fk_lt_refresh_owner",
        ),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    focus: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    focus_version: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    epoch: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    last_refresh_operation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("learning_tutor_operations.id", ondelete="SET NULL")
    )


class LearningTutorOperation(Base, UUIDAuditMixin):
    __tablename__ = "learning_tutor_operations"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_lt_operation_owner"),
        UniqueConstraint(
            "user_id", "epoch", "request_id", name="uq_lt_operation_request"
        ),
        Index(
            "uq_lt_running",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        CheckConstraint("kind IN ('turn','refresh')", name="ck_lt_operation_kind"),
        CheckConstraint(
            "status IN ('running','succeeded','failed')", name="ck_lt_operation_status"
        ),
        CheckConstraint(
            "epoch >= 0 AND revision >= 0 AND input_tokens >= 0 AND output_tokens >= 0",
            name="ck_lt_operation_counts",
        ),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE")
    )
    epoch: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    request_hash: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(Text)
    input: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    output: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    model_admitted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LearningRecommendation(Base, UUIDAuditMixin):
    __tablename__ = "learning_recommendations"
    __table_args__ = (
        Index("ix_learning_recommendations_user", "user_id"),
        Index(
            "uq_lt_active_topic",
            "user_id",
            "kind",
            "normalized_topic",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        ForeignKeyConstraint(
            ["user_id", "operation_id"],
            ["learning_tutor_operations.user_id", "learning_tutor_operations.id"],
            name="fk_lt_recommendation_operation_owner",
        ),
        ForeignKeyConstraint(
            ["user_id", "accepted_path_id"],
            ["paths.user_id", "paths.id"],
            name="fk_lt_recommendation_path_owner",
        ),
        ForeignKeyConstraint(
            ["user_id", "accepted_beat_id"],
            ["beats.user_id", "beats.id"],
            name="fk_lt_recommendation_beat_owner",
        ),
        CheckConstraint("kind IN ('path','beat')", name="ck_lt_recommendation_kind"),
        CheckConstraint(
            "status IN ('active','dismissed','superseded','accepted')",
            name="ck_lt_recommendation_status",
        ),
        CheckConstraint(
            "epoch >= 0 AND focus_version >= 0", name="ck_lt_recommendation_versions"
        ),
        CheckConstraint(
            "(accepted_path_id IS NULL OR kind = 'path') AND "
            "(accepted_beat_id IS NULL OR kind = 'beat')",
            name="ck_lt_recommendation_target_kind",
        ),
        CheckConstraint(
            "(status = 'accepted' AND accepted_at IS NOT NULL AND "
            "accepted_request_hash IS NOT NULL) OR (status <> 'accepted' AND "
            "accepted_at IS NULL AND accepted_request_hash IS NULL AND "
            "accepted_path_id IS NULL AND accepted_beat_id IS NULL)",
            name="ck_lt_recommendation_acceptance",
        ),
        CheckConstraint(
            "status = 'accepted' OR (topic IS NOT NULL AND level IS NOT NULL AND "
            "description IS NOT NULL AND rationale IS NOT NULL AND "
            "evidence IS NOT NULL AND normalized_topic IS NOT NULL)",
            name="ck_lt_recommendation_payload",
        ),
        CheckConstraint(
            "level IS NULL OR level IN ('new_to_it','some_experience','work_in_it')",
            name="ck_lt_recommendation_level",
        ),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE")
    )
    operation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("learning_tutor_operations.id", ondelete="SET NULL")
    )
    epoch: Mapped[int] = mapped_column(Integer)
    focus_version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(Text)
    level: Mapped[str | None] = mapped_column(Text)
    guidance: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[list[dict] | None] = mapped_column(JSONB(none_as_null=True))
    normalized_topic: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="active", server_default="active")
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_path_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("paths.id", ondelete="SET NULL")
    )
    accepted_beat_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("beats.id", ondelete="SET NULL")
    )
    accepted_request_hash: Mapped[str | None] = mapped_column(Text)
