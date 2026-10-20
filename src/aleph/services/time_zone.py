"""Observe the learner's last valid zone independently of request work."""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING

from aleph.repositories.users import UserRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.models import User


@cache
def _known_zones() -> frozenset[str]:
    return frozenset(files("tzdata").joinpath("zones").read_text().splitlines())


def validated_time_zone(header: str | None) -> str | None:
    """Accept only bounded portable IANA names from the pinned tzdata registry."""
    if header and len(header) <= 64 and header in _known_zones():
        return header
    return None


async def observe(session: AsyncSession, user: User, header: str | None) -> None:
    """Finish the short authentication/observation transaction before route work.

    The database predicate, not the request's potentially stale User object,
    decides whether to update. Concurrent equal observations cannot rewrite
    seen-at; a deleted account simply updates no rows. Reuse the authentication
    connection rather than checking out another while retaining the first.
    Committing here keeps the observation durable if downstream work rolls back.
    """
    zone = validated_time_zone(header)
    if zone is not None:
        await UserRepository(session).observe_time_zone(user.id, zone)
    await session.commit()
