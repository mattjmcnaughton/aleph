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

import uuid  # noqa: TC003 - pydantic resolves ShiftRequest's annotations at class-definition time.
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import (  # noqa: TC002 - FastAPI resolves annotations.
    AsyncSession,
)

from aleph.config import MODEL_SLOTS, STUB_MODEL_ID, settings
from aleph.db import get_session
from aleph.models import Lesson

if TYPE_CHECKING:
    from fastapi import FastAPI

# Same alias every ``routers/v1/`` module spells out — a plain FastAPI
# dependency, not the manual generator-draining a raw call to ``get_session()``
# would need. This router is test-only, but it is still real FastAPI wiring
# (``@e2e_router.post``), so it gets the real dependency machinery.
Session = Annotated[AsyncSession, Depends(get_session)]


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
    # ``tutor`` (AL-203/AL-270), ``shaping`` (AL-301/AL-370) and ``streaks``
    # (Phase 5 D7) are all launched and default on in
    # ``services/feature_flags.py``, so the browser suite's plain learner —
    # ``DEV_USER``, who is not an admin and gets none of ``ADMIN_DEFAULT_FLAGS``'
    # baseline — meets both rails and the streak line with nothing set here.
    # This line is kept as an explicit *pin* rather than deleted as redundant:
    # the suite asserts against surfaces that must exist, and "every tutor spec
    # failed on an absent rail" is a confusing way to discover someone flipped a
    # code default.
    settings.feature_flag_defaults = "tutor:on,shaping:on,streaks:on"

    # Imported lazily so mutating settings above lands before app assembly.
    from aleph.app import create_app

    app = create_app()
    # Mounted **only here** — never by ``create_app`` (D11, TDD §11): the
    # production factory has no reference to this module at all, which is the
    # whole guarantee ``tests/unit/test_smoke.py`` pins.
    app.include_router(e2e_router)
    return app
