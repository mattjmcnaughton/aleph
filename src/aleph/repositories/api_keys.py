"""Data access for API keys.

Never opens or commits a transaction; :mod:`aleph.services.api_keys` owns the
unit of work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select, update

from aleph.models.api_key import ApiKey

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession


class ApiKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_user(self, user_id: uuid.UUID) -> list[ApiKey]:
        """Every key, revoked ones included, oldest first."""
        return list(
            await self.session.scalars(
                select(ApiKey)
                .where(ApiKey.user_id == user_id)
                .order_by(ApiKey.created_at, ApiKey.id)
            )
        )

    async def add(self, *, user_id: uuid.UUID, name: str, digest: str) -> ApiKey:
        key = ApiKey(user_id=user_id, name=name, digest=digest)
        self.session.add(key)
        await self.session.flush()
        await self.session.refresh(key)
        return key

    async def revoke_for_user(self, *, key_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Stamp ``revoked_at`` once; a repeat keeps the first stamp.

        ``False`` when the user owns no such key.
        """
        result = await self.session.execute(
            update(ApiKey)
            .where(ApiKey.id == key_id, ApiKey.user_id == user_id)
            .values(revoked_at=func.coalesce(ApiKey.revoked_at, func.now()))
            .returning(ApiKey.id)
        )
        return result.scalar_one_or_none() is not None

    async def active_by_digest(self, digest: str) -> ApiKey | None:
        return await self.session.scalar(
            select(ApiKey).where(ApiKey.digest == digest, ApiKey.revoked_at.is_(None))
        )
