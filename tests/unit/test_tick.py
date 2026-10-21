"""Tick step isolation and timeout behavior, without external services."""

import asyncio

import pytest

from aleph.jobs.tick import run_steps


@pytest.mark.anyio
async def test_main_runs_reminders_before_beats(monkeypatch):
    from aleph.jobs import tick

    calls = []

    async def reminders():
        calls.append("reminders")
        return {}

    async def beats(*args, **kwargs):
        calls.append("beats")
        return {}

    monkeypatch.setattr(tick.notification_service, "reminders", reminders)
    monkeypatch.setattr(tick, "run_beats", beats)
    assert await tick.main() == 0
    assert calls == ["reminders", "beats"]


@pytest.mark.anyio
@pytest.mark.parametrize("timeout", [False, True])
async def test_failed_step_does_not_hide_the_next_step(timeout: bool) -> None:
    calls = []

    async def broken() -> dict[str, int]:
        if timeout:
            await asyncio.sleep(10)
        raise RuntimeError("failed")

    async def healthy() -> dict[str, int]:
        calls.append("healthy")
        return {"claimed": 2}

    assert (
        await run_steps(
            [("broken", broken), ("healthy", healthy)], timeout_seconds=0.01
        )
        == 1
    )
    assert calls == ["healthy"]


@pytest.mark.anyio
async def test_successful_steps_exit_zero() -> None:
    async def healthy() -> dict[str, int]:
        return {"claimed": 0}

    assert await run_steps([("beats", healthy)], timeout_seconds=1) == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "outcome,code", [("failed", 1), ("researching", 1), ("refused", 0), ("skipped", 0)]
)
async def test_pipeline_outcomes_control_exit_code(outcome: str, code: int) -> None:
    async def result() -> dict[str, int]:
        return {outcome: 1}

    assert await run_steps([("beats", result)], timeout_seconds=1) == code
