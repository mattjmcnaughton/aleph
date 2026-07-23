"""Data access for learner accounts (AL-020 auth provisioning).

Constructed per-request with the caller's :class:`AsyncSession` (habagou
convention); the repository never opens or commits transactions — the service
layer owns the unit of work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from aleph.models import User

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class UserRepository:
    """Data access for :class:`~aleph.models.User` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_identity(self, issuer: str, subject: str) -> User | None:
        """Return the account for a stable ``(issuer, subject)`` pair, if any."""
        return await self.session.scalar(
            select(User).where(User.issuer == issuer, User.subject == subject)
        )

    async def username_exists(self, username: str) -> bool:
        """Whether ``username`` is already taken (the column is UNIQUE)."""
        # Fetch at most one id rather than counting the (unique) column: existence
        # only needs a single matching row (habagou's pattern).
        existing = await self.session.scalar(
            select(User.id).where(User.username == username).limit(1)
        )
        return existing is not None

    async def create(
        self,
        *,
        username: str,
        display_name: str,
        issuer: str,
        subject: str,
        email: str | None,
    ) -> User:
        """Insert and flush a new account (caller owns the commit)."""
        user = User(
            username=username,
            display_name=display_name,
            issuer=issuer,
            subject=subject,
            email=email,
        )
        self.session.add(user)
        await self.session.flush()
        return user
