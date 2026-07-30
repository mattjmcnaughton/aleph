"""Data access for per-user feature-flag overrides (AL-203).

Constructed per-request with the caller's :class:`AsyncSession`; the repository
never opens or commits transactions — the service layer owns the unit of work.
Ported from habagou's repository of the same name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aleph.models import UserFeatureOverride

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession


class FeatureFlagRepository:
    """Data access for :class:`~aleph.models.UserFeatureOverride` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def overrides_for_user(self, *, user_id: uuid.UUID) -> dict[str, bool]:
        """All of one user's overrides, keyed by flag key."""
        result = await self.session.execute(
            select(UserFeatureOverride.flag_key, UserFeatureOverride.enabled).where(
                UserFeatureOverride.user_id == user_id
            )
        )
        return {flag_key: enabled for flag_key, enabled in result.all()}

    async def override_counts(self) -> dict[str, int]:
        """Number of per-user overrides per flag key (absent key: zero)."""
        result = await self.session.execute(
            select(UserFeatureOverride.flag_key, func.count()).group_by(
                UserFeatureOverride.flag_key
            )
        )
        return {flag_key: count for flag_key, count in result.all()}

    async def set_override(
        self, *, user_id: uuid.UUID, flag_key: str, enabled: bool
    ) -> None:
        """Create or replace one user's override for a flag (upsert).

        Postgres ``ON CONFLICT`` on the natural primary key, so a repeat set is
        an in-place update rather than a duplicate row or an integrity error —
        the admin UI's "set it again" is the common case, not the exception.
        """
        await self.session.execute(
            pg_insert(UserFeatureOverride)
            .values(user_id=user_id, flag_key=flag_key, enabled=enabled)
            .on_conflict_do_update(
                index_elements=[
                    UserFeatureOverride.user_id,
                    UserFeatureOverride.flag_key,
                ],
                set_={"enabled": enabled, "updated_at": func.now()},
            )
        )

    async def delete_override(self, *, user_id: uuid.UUID, flag_key: str) -> bool:
        """Remove one user's override for a flag; ``False`` if none existed."""
        result = await self.session.execute(
            delete(UserFeatureOverride).where(
                UserFeatureOverride.user_id == user_id,
                UserFeatureOverride.flag_key == flag_key,
            )
        )
        return bool(getattr(result, "rowcount", 0))
