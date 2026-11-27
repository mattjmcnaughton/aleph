"""Data access for API keys.

Never opens or commits a transaction; :mod:`aleph.services.api_keys` owns the
unit of work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, or_, select, update

from aleph.models.api_key import ApiKey

if TYPE_CHECKING:
    import uuid
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


class ApiKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_user(self, user_id: uuid.UUID) -> list[ApiKey]:
        """Every hand-made key, revoked ones included, oldest first."""
        return list(
            await self.session.scalars(
                select(ApiKey)
                .where(ApiKey.user_id == user_id, ApiKey.client_id.is_(None))
                .order_by(ApiKey.created_at, ApiKey.id)
            )
        )

    async def list_connections(self, user_id: uuid.UUID) -> list[ApiKey]:
        """Unrevoked connections whose refresh token is still valid, oldest first."""
        return list(
            await self.session.scalars(
                select(ApiKey)
                .where(
                    ApiKey.user_id == user_id,
                    ApiKey.client_id.is_not(None),
                    ApiKey.revoked_at.is_(None),
                    ApiKey.refresh_expires_at > func.now(),
                )
                .order_by(ApiKey.created_at, ApiKey.id)
            )
        )

    async def add(
        self,
        *,
        user_id: uuid.UUID,
        name: str,
        digest: str,
        client_id: str | None = None,
        expires_at: datetime | None = None,
        refresh_digest: str | None = None,
        refresh_expires_at: datetime | None = None,
    ) -> ApiKey:
        key = ApiKey(
            user_id=user_id,
            name=name,
            digest=digest,
            client_id=client_id,
            expires_at=expires_at,
            refresh_digest=refresh_digest,
            refresh_expires_at=refresh_expires_at,
        )
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
            select(ApiKey).where(
                ApiKey.digest == digest,
                ApiKey.revoked_at.is_(None),
                or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > func.now()),
            )
        )

    async def rotate(
        self,
        *,
        refresh_digest: str,
        client_id: str,
        digest: str,
        new_refresh_digest: str,
        expires_at: datetime,
        refresh_expires_at: datetime,
    ) -> ApiKey | None:
        """Swap a live refresh token for new secrets in one statement.

        Matches only an unrevoked connection of ``client_id`` whose refresh
        token is unexpired, so two concurrent refreshes cannot both win. The
        old refresh digest moves to ``previous_refresh_digest`` for reuse
        detection.
        """
        return await self.session.scalar(
            update(ApiKey)
            .where(
                ApiKey.refresh_digest == refresh_digest,
                ApiKey.client_id == client_id,
                ApiKey.revoked_at.is_(None),
                ApiKey.refresh_expires_at > func.now(),
            )
            .values(
                digest=digest,
                refresh_digest=new_refresh_digest,
                previous_refresh_digest=refresh_digest,
                expires_at=expires_at,
                refresh_expires_at=refresh_expires_at,
                updated_at=func.now(),
            )
            .returning(ApiKey)
        )

    async def revoke_by_previous_refresh(self, refresh_digest: str) -> bool:
        """Revoke the connection whose *replaced* refresh token this is."""
        result = await self.session.execute(
            update(ApiKey)
            .where(ApiKey.previous_refresh_digest == refresh_digest)
            .values(revoked_at=func.coalesce(ApiKey.revoked_at, func.now()))
            .returning(ApiKey.id)
        )
        return result.scalar_one_or_none() is not None

    async def revoke(self, key_id: uuid.UUID) -> None:
        await self.session.execute(
            update(ApiKey)
            .where(ApiKey.id == key_id)
            .values(revoked_at=func.coalesce(ApiKey.revoked_at, func.now()))
        )
