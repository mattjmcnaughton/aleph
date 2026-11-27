"""API key ORM model (a learner's secret for the MCP server)."""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from datetime import (
    datetime,  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
)

from sqlalchemy import DateTime, ForeignKey, Index, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from aleph.db import Base
from aleph.models.base import UUIDAuditMixin


class ApiKey(Base, UUIDAuditMixin):
    """A secret that reaches the learner's account through ``/mcp``.

    Either a key the learner created in Settings, or a **connection**: the
    access token an MCP client got through OAuth (``client_id`` set, see
    ``services/oauth.py``). Only SHA-256 digests are stored (``digest``,
    ``refresh_digest``); secrets are shown once. Revoking stamps
    ``revoked_at`` and keeps the row, so the learner's list stays truthful.

    A connection's access token expires (``expires_at``) and its refresh token
    rotates on every use: ``previous_refresh_digest`` remembers the one just
    replaced, so presenting it again (a stolen token) revokes the connection.
    """

    __tablename__ = "api_keys"
    __table_args__ = (Index("ix_api_keys_user_id", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    digest: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Connections only; all null for a hand-made key, which never expires.
    client_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refresh_digest: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    previous_refresh_digest: Mapped[str | None] = mapped_column(
        Text, nullable=True, unique=True
    )
    refresh_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
