"""Public profile queries: the whole publication chain in every read (TDD D3, D4).

No public read here calls an owner-scoped repository method. Every lookup
names its handle (or, for credit, its id) and checks, in one query, that the
owner's profile is on and the item is Shared and owned by that owner; a child
(a Brief, its Sources) is reached only through that same join. Checking the
parent and then fetching a child by id would make the check meaningless.

The ``public_profiles`` flag is the one link this module does not resolve: it
is a per-owner computation in ``services/feature_flags.py``, which the service
applies to the owner each query returns.

Owner writes (the Handle, the two switches, ``shared``) also live here so the
whole surface has one data-access module. The service owns the transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update

from aleph.models import Beat, Brief, BriefKind, BriefSource, Lesson, Path, Unit, User

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class PublicLessonRow:
    unit_id: uuid.UUID
    title: str
    completed: bool


@dataclass(frozen=True)
class BeatBriefStats:
    count: int
    latest_title: str | None


def _public_owner(handle: str):  # noqa: ANN202 - a SQLAlchemy predicate list.
    return (User.handle == handle, User.profile_public.is_(True))


class PublicProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- public reads ------------------------------------------------------- #

    async def owner(self, handle: str) -> User | None:
        """The owner of a profile that is on, by Handle."""
        return await self.session.scalar(select(User).where(*_public_owner(handle)))

    async def shared_paths(self, user_id: uuid.UUID, *, limit: int) -> list[Path]:
        result = await self.session.execute(
            select(Path)
            .where(Path.user_id == user_id, Path.shared.is_(True))
            .order_by(Path.created_at.desc(), Path.id)
            .limit(limit)
        )
        return list(result.scalars())

    async def shared_beats(self, user_id: uuid.UUID, *, limit: int) -> list[Beat]:
        result = await self.session.execute(
            select(Beat)
            .where(Beat.user_id == user_id, Beat.shared.is_(True))
            .order_by(Beat.created_at.desc(), Beat.id)
            .limit(limit)
        )
        return list(result.scalars())

    async def shared_path(
        self, handle: str, path_id: uuid.UUID
    ) -> tuple[User, Path] | None:
        row = (
            await self.session.execute(
                select(User, Path)
                .join(Path, Path.user_id == User.id)
                .where(
                    *_public_owner(handle), Path.id == path_id, Path.shared.is_(True)
                )
            )
        ).first()
        return (row[0], row[1]) if row else None

    async def shared_beat(
        self, handle: str, beat_id: uuid.UUID
    ) -> tuple[User, Beat] | None:
        row = (
            await self.session.execute(
                select(User, Beat)
                .join(Beat, Beat.user_id == User.id)
                .where(
                    *_public_owner(handle), Beat.id == beat_id, Beat.shared.is_(True)
                )
            )
        ).first()
        return (row[0], row[1]) if row else None

    async def shared_brief(
        self, handle: str, beat_id: uuid.UUID, brief_id: uuid.UUID
    ) -> tuple[User, Beat, Brief] | None:
        """A published Brief, only through its public Beat. Never a Skipped row."""
        row = (
            await self.session.execute(
                select(User, Beat, Brief)
                .join(Beat, Beat.user_id == User.id)
                .join(Brief, Brief.beat_id == Beat.id)
                .where(
                    *_public_owner(handle),
                    Beat.id == beat_id,
                    Beat.shared.is_(True),
                    Brief.id == brief_id,
                    Brief.kind == BriefKind.PUBLISHED,
                )
            )
        ).first()
        return (row[0], row[1], row[2]) if row else None

    async def units(self, path_id: uuid.UUID) -> list[Unit]:
        result = await self.session.execute(
            select(Unit).where(Unit.path_id == path_id).order_by(Unit.position)
        )
        return list(result.scalars())

    async def lessons(self, path_id: uuid.UUID) -> list[PublicLessonRow]:
        """Titles and completion only, in path order: never lesson content."""
        result = await self.session.execute(
            select(Lesson.unit_id, Lesson.title, Lesson.completed_at.is_not(None))
            .where(Lesson.path_id == path_id)
            .order_by(Lesson.position_in_path)
        )
        return [
            PublicLessonRow(unit_id=unit_id, title=title, completed=completed)
            for unit_id, title, completed in result
        ]

    async def lesson_counts(
        self, path_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[int, int]]:
        """``(done, total)`` per path id."""
        if not path_ids:
            return {}
        result = await self.session.execute(
            select(
                Lesson.path_id,
                func.count(Lesson.completed_at),
                func.count(),
            )
            .where(Lesson.path_id.in_(path_ids))
            .group_by(Lesson.path_id)
        )
        counts = {path_id: (0, 0) for path_id in path_ids}
        counts.update({path_id: (done, total) for path_id, done, total in result})
        return counts

    async def brief_stats(
        self, beat_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, BeatBriefStats]:
        """Published Brief count and latest title per Beat."""
        if not beat_ids:
            return {}
        counts: dict[uuid.UUID, int] = {
            beat_id: count
            for beat_id, count in await self.session.execute(
                select(Brief.beat_id, func.count())
                .where(Brief.beat_id.in_(beat_ids), Brief.kind == BriefKind.PUBLISHED)
                .group_by(Brief.beat_id)
            )
        }
        latest: dict[uuid.UUID, str | None] = {
            beat_id: title
            for beat_id, title in await self.session.execute(
                select(Brief.beat_id, Brief.title)
                .where(Brief.beat_id.in_(beat_ids), Brief.kind == BriefKind.PUBLISHED)
                .order_by(Brief.beat_id, Brief.number.desc())
                .distinct(Brief.beat_id)
            )
        }
        return {
            beat_id: BeatBriefStats(
                count=counts.get(beat_id, 0), latest_title=latest.get(beat_id)
            )
            for beat_id in beat_ids
        }

    async def published_briefs(
        self, beat_id: uuid.UUID, *, before_number: int | None, limit: int
    ) -> list[tuple[Brief, int]]:
        """One page of a Beat's published Briefs, newest first, with Source counts.

        The caller has already passed the chain for ``beat_id``.
        """
        sources = (
            select(func.count())
            .where(BriefSource.brief_id == Brief.id)
            .correlate(Brief)
            .scalar_subquery()
        )
        query = (
            select(Brief, sources)
            .where(Brief.beat_id == beat_id, Brief.kind == BriefKind.PUBLISHED)
            .order_by(Brief.number.desc())
            .limit(limit)
        )
        if before_number is not None:
            query = query.where(Brief.number < before_number)
        return [(brief, count) for brief, count in (await self.session.execute(query))]

    async def sources(self, brief_id: uuid.UUID) -> list[BriefSource]:
        result = await self.session.execute(
            select(BriefSource)
            .where(BriefSource.brief_id == brief_id)
            .order_by(BriefSource.position)
        )
        return list(result.scalars())

    # -- credit (D7): by id, the same chain minus the handle ----------------- #

    async def path_with_owner(self, path_id: uuid.UUID) -> tuple[Path, User] | None:
        row = (
            await self.session.execute(
                select(Path, User)
                .join(User, Path.user_id == User.id)
                .where(Path.id == path_id)
            )
        ).first()
        return (row[0], row[1]) if row else None

    async def beat_with_owner(self, beat_id: uuid.UUID) -> tuple[Beat, User] | None:
        row = (
            await self.session.execute(
                select(Beat, User)
                .join(User, Beat.user_id == User.id)
                .where(Beat.id == beat_id)
            )
        ).first()
        return (row[0], row[1]) if row else None

    # -- owner writes -------------------------------------------------------- #

    async def lock_owner(self, user_id: uuid.UUID) -> User | None:
        return await self.session.scalar(
            select(User).where(User.id == user_id).with_for_update()
        )

    async def handle_taken(self, handle: str) -> bool:
        return (
            await self.session.scalar(select(User.id).where(User.handle == handle))
            is not None
        )

    async def set_path_shared(self, path_id: uuid.UUID, *, shared: bool) -> None:
        await self.session.execute(
            update(Path).where(Path.id == path_id).values(shared=shared)
        )

    async def set_beat_shared(self, beat_id: uuid.UUID, *, shared: bool) -> None:
        await self.session.execute(
            update(Beat).where(Beat.id == beat_id).values(shared=shared)
        )
