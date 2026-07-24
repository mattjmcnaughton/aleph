"""Unit tests for the generation lifecycle primitives (AL-041, TDD §5.4).

Pure asyncio behaviour — no DB, no model. The task registry's three load-bearing
properties (strong refs so a task is not GC'd mid-flight, done-callback discard,
cancel + drain on shutdown) and the config guards for the reconciler settings.
The reconciler loop and orchestrator wiring live in the integration tests (they
need real Postgres).
"""

from __future__ import annotations

import asyncio
import contextlib
import gc

import pytest
from structlog.testing import capture_logs

from aleph.config import Settings
from aleph.services.generation import GenerationOrchestrator
from aleph.services.lifecycle import GenerationLifecycle, Reconciler, TaskRegistry


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_registry_holds_strong_ref_so_task_is_not_gc_collected() -> None:
    # A bare create_task whose only reference is dropped can be garbage-collected
    # mid-flight (CPython only weakly references scheduled tasks), silently
    # cancelling the work and swallowing its exception. The registry's strong ref
    # is the guard (§5.4 invariant): the task must still run to completion even
    # after every local reference is dropped and gc is forced.
    registry = TaskRegistry()
    done = asyncio.Event()

    async def work() -> None:
        await asyncio.sleep(0.02)
        done.set()

    registry.spawn(work())
    # Drop every local handle and force a collection: only the registry keeps it.
    gc.collect()

    await asyncio.wait_for(done.wait(), timeout=1.0)
    assert done.is_set()


@pytest.mark.anyio
async def test_registry_discards_task_on_completion() -> None:
    registry = TaskRegistry()

    async def work() -> None:
        return None

    task = registry.spawn(work())
    assert len(registry) == 1  # tracked while live

    await task
    # The done-callback runs on the loop; yield so it fires, then assert removal.
    await asyncio.sleep(0)
    assert len(registry) == 0


@pytest.mark.anyio
async def test_cancel_all_cancels_live_tasks_and_drains() -> None:
    registry = TaskRegistry()
    started = asyncio.Event()

    async def blocks_forever() -> None:
        started.set()
        await asyncio.Event().wait()  # never resolves; only cancellation ends it

    task = registry.spawn(blocks_forever())
    await asyncio.wait_for(started.wait(), timeout=1.0)

    await registry.cancel_all()

    assert task.cancelled()
    assert len(registry) == 0


@pytest.mark.anyio
async def test_cancel_all_is_a_noop_with_no_live_tasks() -> None:
    registry = TaskRegistry()
    await registry.cancel_all()  # must not raise
    assert len(registry) == 0


@pytest.mark.anyio
async def test_spawn_after_close_logs_orphan_warning() -> None:
    # A spawn after ``cancel_all`` (shutdown) lands in a registry nothing will
    # drain again — an orphan that can outlive the process. It cannot be rejected
    # (the seam must keep spawning), so it must at least be *visible*: a warning.
    registry = TaskRegistry()
    await registry.cancel_all()  # closes the registry

    async def work() -> None:
        return None

    with capture_logs() as logs:
        task = registry.spawn(work())
        await task
    assert any(entry["event"] == "task_spawned_after_registry_closed" for entry in logs)


# --------------------------------------------------------------------------- #
# Reconciler loop resilience
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_run_forever_survives_a_tick_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A tick raising an unexpected error (a transient scan blip) must be logged
    # and swallowed — the loop must NOT die on one bad pass. Drive a tiny interval
    # and a tick that raises once, then reaches a second tick: the loop survived.
    reached_second_tick = asyncio.Event()
    calls = 0

    async def flaky_tick() -> list[object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient scan blip")
        reached_second_tick.set()
        return []

    reconciler = Reconciler(
        GenerationOrchestrator(),
        registry=TaskRegistry(),
        interval_seconds=0.001,
        stale_after_seconds=0.05,
    )
    monkeypatch.setattr(reconciler, "tick", flaky_tick)

    loop_task = asyncio.create_task(reconciler.run_forever())
    try:
        await asyncio.wait_for(reached_second_tick.wait(), timeout=1.0)
    finally:
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop_task
    assert calls >= 2  # survived the raising first tick and ticked again


@pytest.mark.anyio
async def test_run_forever_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The counterpart to survival: a CancelledError (BaseException, not caught by
    # ``except Exception``) must propagate so shutdown stops the loop cleanly.
    ticked = asyncio.Event()

    async def signalling_tick() -> list[object]:
        ticked.set()
        return []

    reconciler = Reconciler(
        GenerationOrchestrator(),
        registry=TaskRegistry(),
        interval_seconds=0.001,
        stale_after_seconds=0.05,
    )
    monkeypatch.setattr(reconciler, "tick", signalling_tick)

    loop_task = asyncio.create_task(reconciler.run_forever())
    await asyncio.wait_for(ticked.wait(), timeout=1.0)
    loop_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop_task


# --------------------------------------------------------------------------- #
# GenerationLifecycle start/stop seam round-trip
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_lifecycle_stop_restores_base_seams() -> None:
    # start() binds the orchestrator's runtime seams (spawn → registry, model_slot
    # → semaphore); stop() must restore the construction-time base seams so the
    # shared singleton is clean between an app's shutdown and any later start.
    orch = GenerationOrchestrator()
    base_spawn = orch._spawn
    base_slot = orch._model_slot

    lifecycle = GenerationLifecycle(orch)
    await lifecycle.start()
    # Bound: the seams now point at the lifecycle's registry + semaphore.
    assert orch._spawn is not base_spawn
    assert orch._model_slot is not base_slot
    assert orch._spawn == lifecycle.registry.spawn

    await lifecycle.stop()
    # Restored: exactly the construction-time seams, by identity.
    assert orch._spawn is base_spawn
    assert orch._model_slot is base_slot


@pytest.mark.anyio
async def test_lifecycle_rejects_double_start() -> None:
    # A second start without an intervening stop would leak the first reconciler
    # loop and re-bind already-bound seams — a programming error, so it raises.
    orch = GenerationOrchestrator()
    lifecycle = GenerationLifecycle(orch)
    await lifecycle.start()
    try:
        with pytest.raises(RuntimeError, match="already started"):
            await lifecycle.start()
    finally:
        await lifecycle.stop()


def test_config_defaults_match_tdd_section_14() -> None:
    settings = Settings()
    assert settings.reconciler_interval_seconds == 30.0
    assert settings.max_concurrent_generations == 8


def test_config_rejects_nonpositive_reconciler_interval() -> None:
    with pytest.raises(ValueError, match="reconciler_interval_seconds"):
        Settings(reconciler_interval_seconds=0)


def test_config_rejects_zero_concurrency_bound() -> None:
    with pytest.raises(ValueError, match="max_concurrent_generations"):
        Settings(max_concurrent_generations=0)
