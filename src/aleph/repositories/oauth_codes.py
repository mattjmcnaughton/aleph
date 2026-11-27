"""Data access for OAuth authorization codes.

Never opens or commits a transaction; :mod:`aleph.services.oauth` owns the
unit of work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select, update

from aleph.models.oauth_code import OAuthCode

if TYPE_CHECKING:
    import uuid
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


class OAuthCodeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self,
        *,
        code_digest: str,
        user_id: uuid.UUID,
        client_id: str,
        client_name: str,
        redirect_uri: str,
        code_challenge: str,
        expires_at: datetime,
    ) -> None:
        self.session.add(
            OAuthCode(
                code_digest=code_digest,
                user_id=user_id,
                client_id=client_id,
                client_name=client_name,
                redirect_uri=redirect_uri,
                code_challenge=code_challenge,
                expires_at=expires_at,
            )
        )
        await self.session.flush()

    async def redeem(self, code_digest: str) -> OAuthCode | None:
        """Mark an unused, unexpired code used and return it; else ``None``.

        One statement, so concurrent redemptions of one code cannot both win.
        """
        return await self.session.scalar(
            update(OAuthCode)
            .where(
                OAuthCode.code_digest == code_digest,
                OAuthCode.used_at.is_(None),
                OAuthCode.expires_at > func.now(),
            )
            .values(used_at=func.now(), updated_at=func.now())
            .returning(OAuthCode)
        )

    async def by_digest(self, code_digest: str) -> OAuthCode | None:
        return await self.session.scalar(
            select(OAuthCode).where(OAuthCode.code_digest == code_digest)
        )

    async def link_api_key(self, code_id: uuid.UUID, api_key_id: uuid.UUID) -> None:
        await self.session.execute(
            update(OAuthCode)
            .where(OAuthCode.id == code_id)
            .values(api_key_id=api_key_id)
        )

    async def count_since(self, user_id: uuid.UUID, since: datetime) -> int:
        return (
            await self.session.scalar(
                select(func.count())
                .select_from(OAuthCode)
                .where(OAuthCode.user_id == user_id, OAuthCode.created_at >= since)
            )
            or 0
        )
