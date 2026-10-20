"""Run one finite hourly tick: ``python -m aleph.jobs.tick``."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from aleph import db
from aleph.config import settings
from aleph.logging import configure_logging
from aleph.services.briefing import BriefingService
from aleph.services.retrieval import ExaRetriever
from aleph.services.scheduled_beats import run_beats

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

logger = structlog.get_logger(__name__)


async def run_steps(
    steps: Sequence[tuple[str, Callable[[], Awaitable[dict[str, int]]]]],
    *,
    timeout_seconds: float,
) -> int:
    """Keep steps independent; a failed/expired step cannot hide the next."""
    failed = False
    results = {}
    logger.info("job_tick_started")
    for name, step in steps:
        try:
            async with asyncio.timeout(timeout_seconds):
                counts = await step()
            results[name] = counts
            step_failed = bool(counts.get("failed") or counts.get("researching"))
            failed |= step_failed
            logger.info(
                "job_step_finished",
                step=name,
                counts=counts,
                outcome="failed" if step_failed else "completed",
            )
        except Exception:
            failed = True
            logger.exception("job_step_failed", step=name)
    logger.info("job_tick_finished", failed=failed, counts=results)
    return int(failed)


async def main() -> int:
    configure_logging()
    retriever = (
        ExaRetriever(
            api_key=settings.exa_api_key,
            max_documents=settings.brief_retrieval_max_documents,
        )
        if settings.exa_api_key
        else None
    )
    service = BriefingService(config=settings, retriever=retriever)

    async def beats() -> dict[str, int]:
        return await run_beats(service, config=settings)

    try:
        # Reminder delivery is added by T9 (#269), not a pretend successful step.
        return await run_steps(
            [("beats", beats)], timeout_seconds=settings.job_step_timeout_seconds
        )
    finally:
        await db.dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
