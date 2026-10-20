"""Away-time Beat research using the existing claim and fenced pipeline.

Read candidates in keyset pages, durably advancing past admitted rows. Only a
bounded number of claims/tasks is retained per tick; timeout bounds scan time.
No request session or background web task survives the step.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from aleph.authz import is_admin
from aleph.db import new_session
from aleph.domains.cadence import is_claimable
from aleph.models import BeatResearchState
from aleph.repositories import BeatRepository, BriefRepository
from aleph.repositories.users import UserRepository
from aleph.services.feature_flags import FeatureFlag, FeatureFlagService
from aleph.services.rate_limit import build_daily_rate_limiter

if TYPE_CHECKING:
    import uuid

    from aleph.config import Settings
    from aleph.services.briefing import BriefingService, SessionFactory


async def run_beats(
    service: BriefingService,
    *,
    config: Settings,
    now: datetime | None = None,
    session_factory: SessionFactory = new_session,
) -> dict[str, int]:
    """Acquire a permit before claiming, then await bounded generation.

    The account lock serializes the capacity check with deployment claims; the
    Beat lock ensures cadence is read after a racing worker's settlement. The
    UPDATE claim remains the final cross-process state/fencing authority.
    """
    now = now or datetime.now(UTC)
    counts: Counter[str] = Counter()
    async with session_factory() as session:
        start = after = await BeatRepository(session).scan_frontier()
    wrapped = False
    semaphore = asyncio.Semaphore(config.max_concurrent_generations)

    async def research(beat_id: uuid.UUID, today: date, fence: datetime) -> None:
        await service.run_research(beat_id, today, fence=fence)
        async with session_factory() as session:
            beat = await BeatRepository(session).get(beat_id)
            if beat is None:
                counts["deleted"] += 1
            elif beat.research_state == BeatResearchState.IDLE:
                latest = await BriefRepository(session).list_for_beat(beat_id, limit=1)
                counts[latest[0].kind.value if latest else "failed"] += 1
            else:
                counts[beat.research_state.value] += 1

    async def process(beat_id: uuid.UUID, user_id: uuid.UUID) -> None:
        # Admission acquired the permit and committed the frontier before any
        # account/Beat lock wait. Claim clocks never run while queued.
        try:
            counts["examined"] += 1
            async with session_factory() as session:
                user = await UserRepository(session).lock_by_id(user_id)
                if user is None:
                    return
                flags = await FeatureFlagService(
                    session, config=config
                ).resolve_for_user(user)
                if not flags.get(FeatureFlag.ANALYST, False):
                    counts["disabled"] += 1
                    return
                repo = BeatRepository(
                    session,
                    stale_after_seconds=config.brief_research_stale_after_seconds,
                )
                beat = await repo.lock(beat_id)
                if beat is None:
                    return
                today = now.astimezone(ZoneInfo(user.time_zone or "UTC")).date()
                briefs = BriefRepository(session)
                last = await briefs.last_published_on_by_beat([beat.id])
                if not is_claimable(
                    last.get(beat.id), beat.anchor_weekday, today=today
                ):
                    counts["not_due"] += 1
                    return
                latest = await briefs.latest_published(beat.id)
                if latest is not None and latest.read_at is None:
                    counts["idle"] += 1
                    return
                limiter = build_daily_rate_limiter(session, config=config)
                if not await limiter.brief_research_capacity_available(
                    user_id=user.id, is_admin=is_admin(user, config)
                ):
                    counts["capped"] += 1
                    return
                fence = await repo.claim_research(beat.id)
                await session.commit()
                if fence is None:
                    counts["lost_claim"] += 1
                    return
                counts["claimed"] += 1
            await research(beat_id, today, fence)
        finally:
            semaphore.release()

    # TaskGroup cancels and awaits every child on timeout/failure. Never leave
    # detached tasks writing after the step reports completion.
    while counts["claimed"] < config.job_max_beats_per_tick:
        async with session_factory() as session:
            candidates = await BeatRepository(
                session, stale_after_seconds=config.brief_research_stale_after_seconds
            ).scheduled_candidates(
                after=after,
                until=start if wrapped else None,
                limit=min(
                    config.job_beat_batch_size,
                    config.job_max_beats_per_tick - counts["claimed"],
                ),
            )
        if not candidates:
            if start is not None and not wrapped:
                # Wrap once, stopping at the invocation's initial frontier so
                # ineligible rows cannot make a successful tick scan forever.
                async with session_factory() as session:
                    await BeatRepository(session).checkpoint_scan(None)
                    await session.commit()
                after = None
                wrapped = True
                continue
            break
        async with asyncio.TaskGroup() as tasks:
            for candidate in candidates:
                await semaphore.acquire()
                try:
                    # Do not checkpoint a queued page: its unstarted tail must
                    # be next even if this invocation is cancelled. This tiny
                    # transaction holds no account or Beat locks.
                    async with session_factory() as session:
                        await BeatRepository(session).checkpoint_scan(candidate.id)
                        await session.commit()
                    after = candidate.id
                    tasks.create_task(process(candidate.id, candidate.user_id))
                except BaseException:
                    semaphore.release()
                    raise
    return dict(counts)
