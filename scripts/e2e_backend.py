"""Test-only backend factory for the Playwright e2e harness (TDD §12, AL-003).

The real Aleph app — its routers, services, orchestrator and DB — booted with
**one** substitution: the deterministic stub model (``services/stub_model.py``)
in place of every OpenRouter slot, so the browser suite runs offline and
deterministically. This is the same seam habagou's ``scripts/e2e_backend.py``
uses: the settings mutation happens in :func:`create_stub_app` (invoked by
uvicorn's ``--factory``), not at import time, so importing this module has no
global side effects.

Boot it exactly as the Playwright ``webServer`` block does::

    ENV=test DATABASE_URL=... uv run uvicorn \
        scripts.e2e_backend:create_stub_app --factory --host 127.0.0.1 --port 8000

``ENV=test`` keeps the production stub-guard (``config._forbid_stub_in_production``)
satisfied. The caller is responsible for pointing ``DATABASE_URL`` at a migrated
database (the ``webServer`` command runs ``alembic upgrade head`` first) — this
factory only swaps the model slots and lifts the per-account rate limits so the
two Playwright projects sharing one backend never trip a cap.
"""

from __future__ import annotations

import asyncio
import uuid  # noqa: TC003 - pydantic resolves the Shift*Request classes' annotations at class-definition time.
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import (  # noqa: TC002 - FastAPI resolves annotations.
    AsyncSession,
)

from aleph.config import MODEL_SLOTS, STUB_MODEL_ID, settings
from aleph.db import get_session
from aleph.models import Lesson
from aleph.services.briefing import briefing_service
from aleph.services.retrieval import StubRetriever

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from fastapi import FastAPI

    from aleph.agents.researcher import RetrievedDocument

# Same alias every ``routers/v1/`` module spells out — a plain FastAPI
# dependency, not the manual generator-draining a raw call to ``get_session()``
# would need. This router is test-only, but it is still real FastAPI wiring
# (``@e2e_router.post``), so it gets the real dependency machinery.
Session = Annotated[AsyncSession, Depends(get_session)]


class ControlledRetriever(StubRetriever):
    """Hold selected e2e topics until the browser has observed a pending poll."""

    def __init__(self) -> None:
        self._held: dict[str, asyncio.Event] = {}

    def hold(self, topic: str) -> None:
        self._held[topic] = asyncio.Event()

    def release(self, topic: str) -> None:
        event = self._held.pop(topic, None)
        if event is not None:
            event.set()

    async def search(
        self, queries: Sequence[str], *, since: date | None = None
    ) -> list[RetrievedDocument]:
        for topic, event in list(self._held.items()):
            if any(topic in query for query in queries):
                await event.wait()
        return await super().search(queries, since=since)


class ResearchGateRequest(BaseModel):
    topic: str


class ShiftRequest(BaseModel):
    """Body for ``POST /__e2e__/shift-completions``: which path, how far back.

    Test-only, so it lives here rather than in ``dtos/`` — that package is the
    wire contract for the real app, and this shape is never sent to it. No
    bound (``Field(ge=...)``) on ``days``: a journey deciding "how far back" is
    a test detail, not a boundary this harness needs to defend.
    """

    path_id: uuid.UUID
    days: int


# Phase 5 TDD D11 / §11: the e2e clock. Determinism for W23 (a streak that must
# survive a missed *day boundary*) lives here, in the harness, never behind a
# config guard in real code — the discipline Phase 1 D10 / Phase 2B D12 already
# set. Defined as a **module-level** router rather than inline in
# ``create_stub_app`` so ``tests/unit/test_smoke.py``'s guard has something
# concrete to name in its own docstring, but it is still only ever mounted
# below, in ``create_stub_app`` — nothing in ``aleph.app`` imports this module,
# so the production app builds with no reference to this router at all.
e2e_router = APIRouter()


@e2e_router.post("/__e2e__/hold-research")
async def hold_research(body: ResearchGateRequest, request: Request) -> None:
    request.app.state.retriever.hold(body.topic)


@e2e_router.post("/__e2e__/release-research")
async def release_research(body: ResearchGateRequest, request: Request) -> None:
    request.app.state.retriever.release(body.topic)


@e2e_router.post("/__e2e__/shift-completions")
async def shift_completions(body: ShiftRequest, session: Session) -> None:
    """Backdate a path's completions so a journey can observe "yesterday".

    A **shift** primitive, not a seeder (D11): it fabricates no lessons, moves
    only the one column every reader already treats as the source of truth
    (D1), and so cannot put the database into a state the real app could not
    reach on its own — a learner who genuinely completed a lesson a few days
    ago looks identical on every read. Repeatable: shifting twice by ``1`` is
    the same as shifting once by ``2`` (W23 uses exactly that to go from
    "yesterday" to "the day before").

    No ownership check and no auth dependency — unlike every route in
    ``routers/v1/``, this one is never reachable in production (it is not
    mounted by ``create_app``), and the harness's one learner account is the
    only caller that will ever exist.

    ``make_interval``'s positional args are (years, months, weeks, days, hours,
    mins, secs) — same helper ``LessonRepository.completion_days_for_user``
    uses (Phase 5 TDD §5.2), here with ``days`` (index 3) the only non-zero one.
    """
    await session.execute(
        update(Lesson)
        .where(Lesson.path_id == body.path_id, Lesson.completed_at.isnot(None))
        .values(
            completed_at=Lesson.completed_at - func.make_interval(0, 0, 0, body.days)
        )
    )
    await session.commit()


def create_stub_app() -> FastAPI:
    """Assemble the real app with the stub model wired into every slot.

    Mutates the module-level ``settings`` singleton *before* ``create_app`` runs,
    so the generation orchestrator (which reads the same singleton) resolves
    ``stub`` at run time and the app never reaches a live provider. Rate limits
    are disabled (a cap of 0 disables it, per ``config``) because the e2e
    projects share one backend + user and would otherwise exhaust the daily
    quota.
    """
    # Every slot, from the one list ``config`` also guards production with — a
    # slot stubbed there but missed here would send that surface's calls at a
    # live provider, the one thing this factory exists to prevent.
    for slot in MODEL_SLOTS:
        setattr(settings, slot, STUB_MODEL_ID)
    # Keep the admin picker inside the stub too: an allowlisted real model id
    # would escape the deterministic stub (empty API key) in e2e (AL-052 note),
    # including via the tutor's per-message model override.
    settings.model_allowlist = STUB_MODEL_ID
    settings.rate_limit_paths_per_day = 0
    settings.rate_limit_lesson_generations_per_day = 0
    settings.rate_limit_tutor_messages_per_day = 0
    settings.rate_limit_shaping_messages_per_day = 0
    # Phase 6 (ticket AL-560): the arrival drain's own daily cap (D14) — same
    # "0 disables it" convention as every rate limit above. **The reason is
    # not concurrent projects sharing a user**. W29/W31 run in the
    # `mobile-390x844` project and spend three research runs per suite run
    # (one per test in `w29.spec.ts`, plus `w31.spec.ts`'s own) against
    # `RATE_LIMIT_BRIEF_RESEARCH_PER_DAY`'s default of 5 — fine for a single
    # CI run, but `reuseExistingServer: !CI` keeps `aleph_e2e` warm across
    # local re-runs on the same calendar day, so a second local run that same
    # day would silently stop claiming partway through once the cap is spent.
    settings.rate_limit_brief_research_per_day = 0
    # ``max_beats_per_learner`` is NOT set to 0 here (unlike every cap above):
    # it is a **stock** cap (`check_beat_creation`, live Beat rows), not a
    # daily flow one, and `BriefingService.drain_claimable` reuses the same
    # config value as the arrival drain's own query `LIMIT` (`services/
    # briefing.py`) — 0 would make that `LIMIT 0` and silently stop the drain
    # from ever claiming anything. Raised, not zeroed, for exactly that
    # reason — but only into the low tens, not the thousands (code-review,
    # ticket AL-560 follow-up: the original `1_000` compounded with the cap
    # above into an unbounded local drain). `reuseExistingServer: !CI` keeps
    # `aleph_e2e` warm across local re-runs, and every Beat this suite ever
    # deploys stays a live, claim-eligible row forever after — there is no
    # Beat-deletion control in this ticket's scope — so each one becomes
    # claim-eligible again on its own next anchor weekday, and the very next
    # `GET /beats` that day drains and spawns every one of them at once, one
    # real (if stubbed) research run per row, through
    # `LIMIT max_beats_per_learner`. 30 is comfortably more than this suite
    # ever deploys in one calendar day and keeps that `LIMIT` an actual
    # bound, without leaving a weeks-old local database's accumulated Beats
    # free to fan out into an unbounded drain the next time someone visits
    # home.
    settings.max_beats_per_learner = 30
    # Pin all surfaces exercised by the browser suite, including dark Flow,
    # so code-default changes cannot turn a journey into a flag-gate test.
    settings.feature_flag_defaults = "tutor:on,shaping:on,streaks:on,analyst:on,flow:on"

    # Phase 6's retrieval seam (ticket AL-560; `services/retrieval.py`,
    # `services/briefing.py`). ``briefing_service`` is a module-level
    # singleton constructed with no live ``Retriever`` at all
    # (``_UnconfiguredRetriever``, which always raises) until
    # ``services/lifecycle.py::GenerationLifecycle.start`` runs and rebinds
    # it (see below) — so an e2e Beat's research run would fail before ever
    # reaching the stub model unless this factory sets a live seam first.
    # Setting the private seam directly, before ``create_app()`` assembles the
    # routers that import this exact singleton, mirrors both this factory's
    # own ``settings`` mutations above and the sanctioned test pattern
    # `tests/integration/test_briefing.py::
    # test_lifecycle_binds_briefing_service_to_the_shared_registry` already
    # uses (`monkeypatch.setattr(briefing_service, "_retriever", ...)`) — the
    # object every route in `routers/v1/beats.py` calls into, not a second,
    # disconnected instance.
    #
    # **A coordination hazard at the lifecycle seam, recorded here on purpose**
    # (code-review, ticket AL-560 follow-up; the retriever-wiring fix below
    # landed in 62788db). This assignment runs BEFORE `create_app()` below —
    # and therefore before the app's lifespan runs at all.
    # `services/lifecycle.py::GenerationLifecycle.start` binds a live
    # `ExaRetriever` onto this same `briefing_service._retriever` seam via
    # `bind_runtime(...)`, which only runs INSIDE the lifespan — i.e. AFTER
    # `create_stub_app()` has already returned this line's `StubRetriever()`
    # in place. They compose correctly only because that binding is a
    # documented no-op when `EXA_API_KEY` is unset (true here — this factory
    # never sets it), so the lifespan's own rebind finds nothing to do and
    # leaves this `StubRetriever` standing. **Do not "fix"
    # this ordering** by moving this assignment after `create_app()`, or by
    # assuming the lifespan's bind always no-ops: if `bind_runtime` is ever
    # changed to rebind unconditionally (e.g. to a retriever that does not
    # gate on the API key), it would silently overwrite this `StubRetriever`
    # after startup and send the e2e suite at a live provider. Re-verify this
    # interaction before touching either side of it.
    retriever = ControlledRetriever()
    briefing_service._retriever = retriever  # noqa: SLF001

    # Imported lazily so mutating settings above lands before app assembly.
    from aleph.app import create_app

    app = create_app()
    app.state.retriever = retriever
    # Mounted **only here** — never by ``create_app`` (D11, TDD §11): the
    # production factory has no reference to this module at all, which is the
    # whole guarantee ``tests/unit/test_smoke.py`` pins.
    app.include_router(e2e_router)
    return app
