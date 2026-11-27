"""Issue, list, revoke and verify API keys.

Modelled on accanto's ``services/api_keys.py``: a key is
``secrets.token_urlsafe(32)``, only its SHA-256 digest is stored, it is shown
once at creation, it never expires, and revoking stamps ``revoked_at`` (a
repeat keeps the first stamp). A revoked key fails on its next request.

**A key only works through the MCP server.** ``routers/mcp.py`` forwards each
tool to an ``/api/v1`` route in-process and sets :data:`forwarding` around that
call; ``dependencies.get_current_user`` accepts an API key only while it is
set. Presented to ``/api/v1`` directly, a key is ``401``, so a leaked key can
do what the MCP tools do and nothing else (no deleting paths, no admin, no key
management).

A **connection** is a key an MCP client got through OAuth (``services/oauth.py``):
it carries the client's ``client_id`` and expires after an hour, and the client
renews it with a rotating refresh token. Settings lists connections apart from
hand-made keys and revokes both the same way.
"""

from __future__ import annotations

import hashlib
import secrets
from contextvars import ContextVar
from typing import TYPE_CHECKING

from aleph.models import User
from aleph.repositories.api_keys import ApiKeyRepository

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.models.api_key import ApiKey

forwarding: ContextVar[bool] = ContextVar("api_key_forwarding", default=False)


def is_api_key(token: str) -> bool:
    """An API key, not an OIDC access token: a JWT always has two dots, and
    ``token_urlsafe`` output never has one."""
    return "." not in token


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


async def list_keys(session: AsyncSession, user_id: uuid.UUID) -> list[ApiKey]:
    """Hand-made keys, revoked ones included."""
    return await ApiKeyRepository(session).list_for_user(user_id)


async def list_connections(session: AsyncSession, user_id: uuid.UUID) -> list[ApiKey]:
    """Connected apps (``services/oauth.py``) that can still refresh."""
    return await ApiKeyRepository(session).list_connections(user_id)


async def issue_key(
    session: AsyncSession, *, user_id: uuid.UUID, name: str
) -> tuple[ApiKey, str]:
    """The new key's row and its secret; the secret is never readable again."""
    secret = secrets.token_urlsafe(32)
    key = await ApiKeyRepository(session).add(
        user_id=user_id, name=name, digest=digest(secret)
    )
    await session.commit()
    return key, secret


async def revoke_key(
    session: AsyncSession, *, user_id: uuid.UUID, key_id: uuid.UUID
) -> bool:
    """Revoke the caller's key; ``False`` when they own no such key."""
    revoked = await ApiKeyRepository(session).revoke_for_user(
        key_id=key_id, user_id=user_id
    )
    await session.commit()
    return revoked


async def authenticate(session: AsyncSession, secret: str) -> User | None:
    """The account an unrevoked, unexpired key belongs to, else ``None``."""
    key = await ApiKeyRepository(session).active_by_digest(digest(secret))
    if key is None:
        return None
    return await session.get(User, key.user_id)
