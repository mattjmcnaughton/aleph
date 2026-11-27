"""OAuth authorization code ORM model (``/oauth/authorize`` -> ``/oauth/token``)."""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from datetime import (
    datetime,  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
)

from sqlalchemy import DateTime, ForeignKey, Index, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from aleph.db import Base
from aleph.models.base import UUIDAuditMixin


class OAuthCode(Base, UUIDAuditMixin):
    """A one-time authorization code a learner approved for an MCP client.

    Only its SHA-256 is stored. It is redeemed at most once (``used_at``) and
    within a minute (``expires_at``); ``api_key_id`` is the connection it
    produced, revoked if the code is ever presented again (OAuth 2.1 §4.1.3).
    Rows also count approvals for the per-learner rate limit.
    """

    __tablename__ = "oauth_codes"
    __table_args__ = (
        Index("ix_oauth_codes_user_id_created_at", "user_id", "created_at"),
    )

    code_digest: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    client_name: Mapped[str] = mapped_column(Text, nullable=False)
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    code_challenge: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
    )
