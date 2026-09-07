"""Per-learner settings ORM model (CONTEXT.md: Settings / Auto-draft).

One row per account, keyed on ``user_id``. Every setting is a plain column
with a server default equal to its code default, so a learner who has never
touched Settings has **no row** and resolves to the defaults at read time
(:mod:`aleph.services.user_settings`) — the same "absent means default" rule
``user_feature_overrides`` already follows for flags.
"""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from datetime import (
    datetime,  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
)

from sqlalchemy import Boolean, DateTime, ForeignKey, Uuid, func, true
from sqlalchemy.orm import Mapped, mapped_column

from aleph.db import Base


class UserSettings(Base):
    """A learner's Settings: the per-account preferences that shape their
    experience.

    Deliberately **not** a :class:`~aleph.models.base.UUIDAuditMixin` table,
    on ``UserFeatureOverride``'s reasoning: ``user_id`` is the natural key and
    the primary key, which is what makes the write path a plain ``ON
    CONFLICT`` upsert and caps the table at one row per learner. A surrogate id
    would allow two contradictory rows for one account. ``created_at`` is not
    carried either — Settings are a current state, not an event log.

    A row is created lazily, by the first ``PATCH /settings``; until then the
    learner has no row and the service answers with the code defaults. Adding a
    setting is one nullable-free column with a server default plus one field on
    the view/DTO — never a backfill.
    """

    __tablename__ = "user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Auto-draft (CONTEXT.md): whether Aleph drafts flashcards from a lesson as
    # it opens (Phase 3 TDD D5) or only when the learner asks. On by default —
    # the launched Phase 3 behaviour is the default experience.
    auto_draft_flashcards: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )
    # Server-side only, as on ``UserFeatureOverride``: the sole write path is
    # the repository's ``ON CONFLICT`` upsert, which sets ``updated_at`` in SQL.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
