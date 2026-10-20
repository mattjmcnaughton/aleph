"""Real Postgres schedule/claim invariants; model and retrieval I/O are fakes."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import func, select, update

from aleph import db
from aleph.config import Settings
from aleph.jobs.tick import run_steps
from aleph.models import BeatResearchRun, BeatResearchState, User
from aleph.repositories import BriefRepository
from aleph.services.briefing import BriefingService
from aleph.services.scheduled_beats import run_beats

from .conftest import CollectingSpawn, create_user
from .test_beats_api import _seed_published, _seed_skipped
from .test_briefing import (
    _briefs_for_beat,
    _create_user,
    _doc,
    _FakeRetriever,
    _force_state,
    _make_beat,
    _make_service,
    _PipelineResponder,
    _reload_beat,
    _resolver,
)


def skip_service(config: Settings | None = None):
    return _make_service(
        retriever=_FakeRetriever(documents=[_doc("https://example.com/a")]),
        resolve_model_fn=_resolver(
            FunctionModel(
                _PipelineResponder(
                    researcher=("findings", {"findings": []}),
                    analyst=("detail", {"detail": ""}),
                )
            )
        ),
        config=config,
    )[0]


@pytest.mark.anyio
@pytest.mark.parametrize("zone", [None, "America/Los_Angeles"])
async def test_24_ticks_honor_local_anchor_and_skipped_is_not_idle(zone) -> None:
    user = await _create_user()
    async with db.async_session() as session:
        await session.execute(
            update(User).where(User.id == user.id).values(time_zone=zone)
        )
        await session.commit()
    beat_id = await _make_beat(user=user, anchor_weekday=0)
    await _seed_skipped(beat_id, published_on=date(2026, 7, 27))
    service = skip_service()
    start = datetime(2026, 8, 3, tzinfo=UTC)
    claims = 0
    for hour in range(24):
        result = await run_beats(
            service, config=Settings(), now=start + timedelta(hours=hour)
        )
        claims += result.get("claimed", 0)
        if zone and hour < 7:
            assert claims == 0  # still Sunday in Los Angeles
    assert claims == 1
    entries = await _briefs_for_beat(beat_id)
    assert len(entries) == 2
    assert {entry.published_on for entry in entries} == {
        date(2026, 7, 27),
        date(2026, 8, 3),
    }


@pytest.mark.anyio
async def test_idle_latest_published_not_skipped_and_read_resumes() -> None:
    user = await _create_user()
    beat_id = await _make_beat(user=user)
    brief_id = await _seed_published(
        beat_id, number=1, published_on=date(2026, 7, 20), url="https://example.com/old"
    )
    await _seed_skipped(beat_id, published_on=date(2026, 7, 27))
    service = skip_service()
    now = datetime(2026, 8, 3, 12, tzinfo=UTC)
    assert (await run_beats(service, config=Settings(), now=now))["idle"] == 1
    async with db.async_session() as session:
        await BriefRepository(session).mark_read(brief_id)
        await session.commit()
    assert (await run_beats(service, config=Settings(), now=now))["claimed"] == 1


@pytest.mark.anyio
async def test_pages_advance_past_idle_and_cap_bounds_claims() -> None:
    user = await _create_user()
    ids = [await _make_beat(user=user) for _ in range(4)]
    # UUID ordering, not fixture creation order, is the keyset cursor.
    first, *due = sorted(ids)
    await _seed_published(
        first, number=1, published_on=date(2026, 7, 20), url="https://example.com/old"
    )
    config = Settings(
        job_beat_batch_size=1,
        job_max_beats_per_tick=2,
        rate_limit_brief_research_per_day=1,
    )
    service = skip_service(config)
    result = await run_beats(
        service, config=config, now=datetime(2026, 8, 3, tzinfo=UTC)
    )
    assert result["idle"] == 1
    assert result["claimed"] == 1
    assert result["capped"] == 2
    assert result["examined"] == 4
    async with db.async_session() as session:
        assert (
            await session.scalar(select(func.count()).select_from(BeatResearchRun)) == 1
        )


@pytest.mark.anyio
async def test_claim_limit_does_not_starve_next_page() -> None:
    user = await _create_user()
    ids = [await _make_beat(user=user) for _ in range(3)]
    config = Settings(job_beat_batch_size=1, job_max_beats_per_tick=1)
    service = skip_service(config)
    for _ in ids:
        result = await run_beats(
            service, config=config, now=datetime(2026, 8, 3, tzinfo=UTC)
        )
        assert result["claimed"] == 1
    assert all([len(await _briefs_for_beat(beat_id)) == 1 for beat_id in ids])


@pytest.mark.anyio
async def test_failed_refused_and_live_claim_are_not_retried_but_stale_is() -> None:
    user = await _create_user()
    ids = {}
    for state in (
        BeatResearchState.FAILED,
        BeatResearchState.REFUSED,
        BeatResearchState.RESEARCHING,
    ):
        ids[state] = await _make_beat(user=user)
        await _force_state(
            ids[state], research_state=state, research_started_at=datetime.now(UTC)
        )
    stale = await _make_beat(user=user)
    await _force_state(
        stale,
        research_state=BeatResearchState.RESEARCHING,
        research_started_at=datetime.now(UTC) - timedelta(hours=2),
    )
    result = await run_beats(skip_service(), config=Settings())
    assert result["claimed"] == 1
    assert len(await _briefs_for_beat(stale)) == 1
    for state, beat_id in ids.items():
        assert (await _reload_beat(beat_id)).research_state == state


@pytest.mark.anyio
async def test_racing_tick_and_initial_request_and_late_initial_have_one_run() -> None:
    user = await _create_user()
    beat_id = await _make_beat(user=user)
    config = Settings()
    spawn = CollectingSpawn(hold=True)
    service = skip_service(config)
    # Use the real pipeline, but park request-spawned work so the race is deterministic.
    service.bind_runtime(spawn=spawn, model_slot=lambda: asyncio.Semaphore(2))
    today = datetime.now(UTC).date()
    await asyncio.gather(
        run_beats(service, config=config),
        service.trigger_initial(beat_id, user_id=user.id, local_today=today),
    )
    await spawn.drain()
    # The job may have finished before the request arrives: that must also be a no-op.
    await service.trigger_initial(beat_id, user_id=user.id, local_today=today)
    assert len(await _briefs_for_beat(beat_id)) == 1
    async with db.async_session() as session:
        assert (
            await session.scalar(select(func.count()).select_from(BeatResearchRun)) == 1
        )


@pytest.mark.anyio
async def test_job_generation_concurrency_is_bounded() -> None:
    async with db.async_session() as session:
        users = [await create_user(session, username=f"bounded-{i}") for i in range(5)]
        await session.commit()
    for user in users:
        await _make_beat(user=user)
    config = Settings(max_concurrent_generations=2)
    service = skip_service(config)
    running = peak = 0
    at_capacity, exceeded, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class RecordingService(BriefingService):
        async def run_research(self, *args, **kwargs):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            if running == 2:
                at_capacity.set()
            if running > 2:
                exceeded.set()
            try:
                await release.wait()
                await service.run_research(*args, **kwargs)
            finally:
                running -= 1

    task = asyncio.create_task(run_beats(RecordingService(), config=config))
    try:
        await asyncio.wait_for(at_capacity.wait(), 5)
        # Hold both generators while independent accounts remain eligible. A
        # third entry is a violation, not a timing-dependent overlap of models.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(exceeded.wait(), 1)
    finally:
        release.set()
        result = await asyncio.wait_for(task, 10)
    assert result["skipped"] == 5
    assert peak == 2


@pytest.mark.anyio
async def test_step_timeout_cancels_children_and_next_tick_recovers_stale_claim() -> (
    None
):
    user = await _create_user()
    beat_id = await _make_beat(user=user)
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    class InterruptedService(BriefingService):
        async def run_research(self, *args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    config = Settings()

    async def beats():
        return await run_beats(InterruptedService(), config=config)

    # Include claim DB work and the stalled pipeline in the step deadline.
    task = asyncio.create_task(run_steps([("beats", beats)], timeout_seconds=1))
    await asyncio.wait_for(entered.wait(), 2)
    assert await task == 1
    assert cancelled.is_set()
    assert (await _reload_beat(beat_id)).research_state == BeatResearchState.RESEARCHING
    await _force_state(
        beat_id,
        research_state=BeatResearchState.RESEARCHING,
        research_started_at=datetime.now(UTC) - timedelta(hours=2),
    )
    assert (await run_beats(skip_service(), config=config))["skipped"] == 1
    assert len(await _briefs_for_beat(beat_id)) == 1


@pytest.mark.anyio
async def test_analyst_kill_switch_skips_scheduled_work() -> None:
    user = await _create_user()
    beat_id = await _make_beat(user=user)
    config = Settings(feature_flag_defaults="analyst:off")
    result = await run_beats(skip_service(config), config=config)
    assert result["disabled"] == 1
    assert (await _reload_beat(beat_id)).research_started_at is None


@pytest.mark.anyio
async def test_timed_ticks_resume_admitted_work_across_processes(
    isolated_database,
) -> None:
    async with db.async_session() as session:
        users = [await create_user(session, username=f"scan-{i}") for i in range(4)]
        await session.commit()
    ids = [await _make_beat(user=user) for user in users]
    ordered = sorted(zip(ids, users, strict=True))
    prefix, due = ordered[:-1], ordered[-1][0]
    for beat_id, _ in prefix:
        await _seed_published(
            beat_id,
            number=1,
            published_on=date(2026, 7, 20),
            url="https://example.com/unchanged",
        )
    code = """
import asyncio
from aleph import db
from aleph.config import Settings
from aleph.jobs.tick import run_steps
from aleph.services.briefing import BriefingService
from aleph.services.scheduled_beats import run_beats

class InterruptedService(BriefingService):
    async def run_research(self, *args, **kwargs):
        await asyncio.Event().wait()

async def main():
    config = Settings(max_concurrent_generations=1, job_beat_batch_size=4)
    async def beats():
        return await run_beats(InterruptedService(), config=config)
    try:
        return await run_steps([("beats", beats)], timeout_seconds=0.5)
    finally:
        await db.dispose_engine()

raise SystemExit(asyncio.run(main()))
"""
    # Hold an unchanged, ineligible UUID prefix behind account locks. Each fresh
    # process must checkpoint just the admitted candidate, not its queued page.
    async with db.async_session() as locks:
        await locks.execute(
            select(User)
            .where(User.id.in_([user.id for _, user in prefix]))
            .with_for_update()
        )
        for _ in ordered:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                code,
                env={**os.environ, "DATABASE_URL": isolated_database},
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(process.communicate(), 15)
            assert process.returncode == 1, stderr.decode()
        assert (await _reload_beat(due)).research_state == BeatResearchState.RESEARCHING
        assert not await _briefs_for_beat(due)
    # Cancellation left one fenced claim, not a settlement or detached writer.
    await _force_state(
        due,
        research_state=BeatResearchState.RESEARCHING,
        research_started_at=datetime.now(UTC) - timedelta(hours=2),
    )
    await run_beats(skip_service(), config=Settings())
    assert len(await _briefs_for_beat(due)) == 1
    async with db.async_session() as session:
        assert (
            await session.scalar(select(func.count()).select_from(BeatResearchRun)) == 2
        )


def test_module_entrypoint_boots_and_exits_against_empty_disposable_db(
    isolated_database,
) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "aleph.jobs.tick"],
        env={
            **os.environ,
            "DATABASE_URL": isolated_database,
            "EXA_API_KEY": "",
            "OPENROUTER_API_KEY": "",
            "LOGFIRE_TOKEN": "",
            "LOG_FORMAT": "json",
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert '"event": "job_tick_started"' in result.stdout
    assert '"event": "job_tick_finished"' in result.stdout
