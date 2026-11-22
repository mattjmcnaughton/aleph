"""Read and replace the account's About you (CONTEXT.md: *About you*).

Forward-only by construction: every generation reads the column when it runs,
so a write here is the whole effect. Nothing is reset or regenerated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aleph.repositories import UserRepository

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession


async def get_about_you(session: AsyncSession, user_id: uuid.UUID) -> str | None:
    return await UserRepository(session).about_you(user_id)


async def put_about_you(
    session: AsyncSession, user_id: uuid.UUID, about_you: str | None
) -> str | None:
    await UserRepository(session).set_about_you(user_id, about_you)
    await session.commit()
    return about_you
