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

from typing import TYPE_CHECKING

from aleph.config import MODEL_SLOTS, STUB_MODEL_ID, settings

if TYPE_CHECKING:
    from fastapi import FastAPI


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
    # ``tutor`` (AL-203/AL-270) and ``shaping`` (AL-301/AL-370) are launched and
    # default on in ``services/feature_flags.py``, so the browser suite's plain
    # learner meets both rails with nothing set here. This line is kept as an
    # explicit *pin* rather than deleted as redundant: the suite asserts against
    # surfaces that must exist, and "every tutor spec failed on an absent rail"
    # is a confusing way to discover someone flipped a code default.
    settings.feature_flag_defaults = "tutor:on,shaping:on"

    # Imported lazily so mutating settings above lands before app assembly.
    from aleph.app import create_app

    return create_app()
