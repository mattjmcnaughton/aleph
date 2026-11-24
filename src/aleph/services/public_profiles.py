"""Public profiles: anonymous reads of a published projection, and the owner's
settings (Public profiles TDD; CONTEXT.md: *Public profile*, *Handle*, *Shared*,
*Copy*; Constitution §1's bounded exception).

Every public read goes through :class:`PublicProfileService`, which applies the
one link the repository cannot: the ``public_profiles`` flag, resolved for the
**profile owner** (D9). Any missing link returns ``None``; the router turns
every ``None`` into the same ``404`` (D5). Public reads never write, never
spawn generation, and never see the caller's session (the router has no auth
dependency at all).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.exc import IntegrityError

from aleph.config import settings as global_settings
from aleph.domains.public_profiles import (
    Credit,
    Handle,
    HandleRejected,
    SourceItem,
    credit_for,
    validate_handle,
)
from aleph.dtos.public import (
    PublicActivityCellDTO,
    PublicActivityDTO,
    PublicBeatDTO,
    PublicBeatSummaryDTO,
    PublicBriefDTO,
    PublicBriefEntryDTO,
    PublicLessonDTO,
    PublicOwnerDTO,
    PublicPathDTO,
    PublicPathSummaryDTO,
    PublicProfileDTO,
    PublicSourceDTO,
    PublicUnitDTO,
)
from aleph.repositories.public_profiles import PublicProfileRepository
from aleph.services.feature_flags import FeatureFlag, FeatureFlagService
from aleph.services.progress_read import load_progress_summary

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.config import Settings
    from aleph.models import User

# TDD §8: bounds on what one anonymous read can cost.
MAX_SHARED_ITEMS = 50
BRIEF_PAGE_SIZE = 20
_CADENCE = "weekly"


class HandleTakenError(Exception):
    """Another account already holds the Handle."""


class HandleLockedError(Exception):
    """This account already has a different Handle; it never changes (D8)."""


class HandleInvalidError(Exception):
    def __init__(self, rejected: HandleRejected) -> None:
        super().__init__(rejected.reason)
        self.reason = rejected.reason


class HandleRequiredError(Exception):
    """A profile cannot be turned on without a Handle."""


@dataclass(frozen=True)
class OwnerSettings:
    handle: str | None
    profile_public: bool
    profile_show_activity: bool


def _owner_dto(user: User) -> PublicOwnerDTO:
    assert user.handle is not None
    return PublicOwnerDTO(display_name=user.display_name, handle=user.handle)


def _tz_offset_minutes(user: User, now: datetime) -> int:
    """The owner's ``getTimezoneOffset()`` equivalent from their last-seen zone.

    A visitor's zone is never used: a profile shows the owner's days.
    """
    if not user.time_zone:
        return 0
    try:
        offset = now.astimezone(ZoneInfo(user.time_zone)).utcoffset()
    except ZoneInfoNotFoundError:
        return 0
    return -int(offset.total_seconds() // 60) if offset is not None else 0


class PublicProfileService:
    def __init__(
        self, session: AsyncSession, *, config: Settings = global_settings
    ) -> None:
        self.session = session
        self.config = config
        self.repository = PublicProfileRepository(session)

    async def enabled_for(self, user: User) -> bool:
        flags = await FeatureFlagService(
            self.session, config=self.config
        ).resolve_for_user(user)
        return flags.get(FeatureFlag.PUBLIC_PROFILES, False)

    async def _public_owner(self, handle: str) -> User | None:
        owner = await self.repository.owner(handle)
        if owner is None or not await self.enabled_for(owner):
            return None
        return owner

    # -- public reads -------------------------------------------------------- #

    async def profile(self, handle: str) -> PublicProfileDTO | None:
        owner = await self._public_owner(handle)
        if owner is None:
            return None
        paths = await self.repository.shared_paths(owner.id, limit=MAX_SHARED_ITEMS)
        beats = await self.repository.shared_beats(owner.id, limit=MAX_SHARED_ITEMS)
        counts = await self.repository.lesson_counts([path.id for path in paths])
        stats = await self.repository.brief_stats([beat.id for beat in beats])
        summary = await load_progress_summary(
            self.session,
            user_id=owner.id,
            tz_offset_minutes=_tz_offset_minutes(owner, datetime.now(UTC)),
        )
        path_streaks = {row.path_id: row.current_streak for row in summary.paths}
        activity = (
            PublicActivityDTO(
                current_streak=summary.current_streak,
                best_streak=summary.best_streak,
                activity=[
                    PublicActivityCellDTO(date=cell.day, count=cell.count)
                    for cell in summary.activity
                ],
            )
            if owner.profile_show_activity
            else None
        )
        return PublicProfileDTO(
            display_name=owner.display_name,
            handle=handle,
            member_since=owner.created_at.date(),
            activity=activity,
            paths=[
                PublicPathSummaryDTO(
                    id=path.id,
                    title=path.display_title,
                    level=path.level,
                    done=counts[path.id][0],
                    total=counts[path.id][1],
                    current_streak=path_streaks.get(path.id, 0),
                )
                for path in paths
            ],
            beats=[
                PublicBeatSummaryDTO(
                    id=beat.id,
                    topic=beat.topic,
                    level=beat.level,
                    cadence=_CADENCE,
                    latest_brief_title=stats[beat.id].latest_title,
                    brief_count=stats[beat.id].count,
                )
                for beat in beats
            ],
        )

    async def path(self, handle: str, path_id: uuid.UUID) -> PublicPathDTO | None:
        found = await self.repository.shared_path(handle, path_id)
        if found is None or not await self.enabled_for(found[0]):
            return None
        owner, path = found
        units = await self.repository.units(path.id)
        lessons = await self.repository.lessons(path.id)
        current_index = next(
            (index for index, lesson in enumerate(lessons) if not lesson.completed),
            None,
        )
        by_unit: dict[uuid.UUID, list[PublicLessonDTO]] = defaultdict(list)
        for index, lesson in enumerate(lessons):
            by_unit[lesson.unit_id].append(
                PublicLessonDTO(
                    title=lesson.title,
                    complete=lesson.completed,
                    current=index == current_index,
                )
            )
        return PublicPathDTO(
            id=path.id,
            owner=_owner_dto(owner),
            title=path.display_title,
            topic=path.topic,
            level=path.level,
            guidance=path.guidance,
            teaching_style=path.teaching_style,
            done=sum(lesson.completed for lesson in lessons),
            total=len(lessons),
            units=[
                PublicUnitDTO(title=unit.title, lessons=by_unit[unit.id])
                for unit in units
            ],
        )

    async def beat(
        self, handle: str, beat_id: uuid.UUID, *, cursor: int | None
    ) -> PublicBeatDTO | None:
        found = await self.repository.shared_beat(handle, beat_id)
        if found is None or not await self.enabled_for(found[0]):
            return None
        owner, beat = found
        page = await self.repository.published_briefs(
            beat.id, before_number=cursor, limit=BRIEF_PAGE_SIZE + 1
        )
        more = len(page) > BRIEF_PAGE_SIZE
        page = page[:BRIEF_PAGE_SIZE]
        entries = []
        for brief, source_count in page:
            assert brief.number is not None and brief.title is not None
            entries.append(
                PublicBriefEntryDTO(
                    id=brief.id,
                    number=brief.number,
                    published_on=brief.published_on,
                    title=brief.title,
                    source_count=source_count,
                )
            )
        return PublicBeatDTO(
            id=beat.id,
            owner=_owner_dto(owner),
            topic=beat.topic,
            level=beat.level,
            guidance=beat.guidance,
            cadence=_CADENCE,
            briefs=entries,
            next_cursor=entries[-1].number if more else None,
        )

    async def brief(
        self, handle: str, beat_id: uuid.UUID, brief_id: uuid.UUID
    ) -> PublicBriefDTO | None:
        found = await self.repository.shared_brief(handle, beat_id, brief_id)
        if found is None or not await self.enabled_for(found[0]):
            return None
        owner, beat, brief = found
        assert brief.number is not None
        assert brief.title is not None and brief.body_markdown is not None
        sources = await self.repository.sources(brief.id)
        return PublicBriefDTO(
            id=brief.id,
            beat_id=beat.id,
            beat_topic=beat.topic,
            owner=_owner_dto(owner),
            number=brief.number,
            published_on=brief.published_on,
            title=brief.title,
            body_markdown=brief.body_markdown,
            sources=[
                PublicSourceDTO(
                    position=source.position,
                    publisher=source.publisher,
                    title=source.title,
                    published_on=source.published_on,
                    url=source.url,
                )
                for source in sources
            ],
        )

    # -- credit (D7) --------------------------------------------------------- #

    async def _credit(self, item_id: uuid.UUID, found: tuple | None) -> Credit | None:
        if found is None:
            return None
        item, owner = found
        return credit_for(
            SourceItem(
                item_id=item_id,
                shared=item.shared,
                owner_display_name=owner.display_name,
                owner_handle=owner.handle,
                owner_profile_public=owner.profile_public,
                profiles_enabled=await self.enabled_for(owner),
            )
        )

    async def path_credit(self, source_path_id: uuid.UUID | None) -> Credit | None:
        if source_path_id is None:
            return None
        return await self._credit(
            source_path_id, await self.repository.path_with_owner(source_path_id)
        )

    async def beat_credit(self, source_beat_id: uuid.UUID | None) -> Credit | None:
        if source_beat_id is None:
            return None
        return await self._credit(
            source_beat_id, await self.repository.beat_with_owner(source_beat_id)
        )

    # -- owner settings ------------------------------------------------------ #

    async def settings_for(self, user: User) -> OwnerSettings:
        return OwnerSettings(
            handle=user.handle,
            profile_public=user.profile_public,
            profile_show_activity=user.profile_show_activity,
        )

    async def update_settings(
        self,
        user_id: uuid.UUID,
        *,
        handle: str | None,
        profile_public: bool,
        profile_show_activity: bool,
    ) -> OwnerSettings:
        """Set the three fields in one transaction (TDD §4).

        The Handle is written only while it is ``NULL`` (D8). Sending the
        current Handle again is not a change; sending another one is
        :class:`HandleLockedError`. The unique constraint is the race backstop for
        two accounts claiming one Handle at once.
        """
        owner = await self.repository.lock_owner(user_id)
        assert owner is not None, "the caller is authenticated"
        if handle is not None:
            checked = validate_handle(handle)
            if isinstance(checked, HandleRejected):
                raise HandleInvalidError(checked)
            assert isinstance(checked, Handle)
            if owner.handle is None:
                if await self.repository.handle_taken(checked.value):
                    raise HandleTakenError
                owner.handle = checked.value
            elif owner.handle != checked.value:
                raise HandleLockedError
        if profile_public and owner.handle is None:
            raise HandleRequiredError
        owner.profile_public = profile_public
        owner.profile_show_activity = profile_show_activity
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise HandleTakenError from exc
        return await self.settings_for(owner)

    async def set_path_shared(self, path_id: uuid.UUID, *, shared: bool) -> None:
        await self.repository.set_path_shared(path_id, shared=shared)
        await self.session.commit()

    async def set_beat_shared(self, beat_id: uuid.UUID, *, shared: bool) -> None:
        await self.repository.set_beat_shared(beat_id, shared=shared)
        await self.session.commit()
