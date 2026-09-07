"""Data access for per-learner settings: ``user_settings``.

Constructed per-request with the caller's :class:`AsyncSession`; the repository
never opens or commits transactions — the service layer owns the unit of work
(the ``FeatureFlagRepository`` shape, reused).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aleph.models import UserSettings

if TYPE_CHECKING:
    import uuid
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession


class UserSettingsRepository:
    """Data access for :class:`~aleph.models.UserSettings` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_user(self, user_id: uuid.UUID) -> UserSettings | None:
        """The learner's row, or ``None`` when they have never changed a
        setting (the service reads ``None`` as "every default")."""
        return await self.session.scalar(
            select(UserSettings).where(UserSettings.user_id == user_id)
        )

    async def upsert(
        self, *, user_id: uuid.UUID, changes: Mapping[str, object]
    ) -> UserSettings:
        """Create or update the learner's row with ``changes`` (column -> value).

        Postgres ``ON CONFLICT`` on the primary key: a first change inserts a
        row whose *other* columns take their server defaults, and every later
        change updates only the columns named — a partial ``PATCH`` never
        resets a setting it did not mention. ``changes`` must not be empty;
        the service skips the write entirely for an empty patch.
        """
        if not changes:
            raise ValueError("upsert needs at least one setting to change")
        statement = (
            pg_insert(UserSettings)
            .values(user_id=user_id, **changes)
            .on_conflict_do_update(
                index_elements=[UserSettings.user_id],
                set_={**changes, "updated_at": func.now()},
            )
            .returning(UserSettings)
        )
        row = await self.session.scalar(statement)
        assert row is not None  # ``RETURNING`` on an upsert always yields the row.
        return row
