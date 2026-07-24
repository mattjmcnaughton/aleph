"""Agent eval harness (dev-only, never packaged) — see docs/evals.md.

Runs the agents in ``src/aleph/agents/`` against the curated seed set
(``seed_set.yaml``, TDD §11 / PRD §9) and scores the generations twice over:
Layer 1's free deterministic pre-filters (``generation.py``) and Layer 2's
binary ``MODEL_JUDGE`` rubric judge (``rubric.py``, ``calibration.py``,
``judge.py``), with judge↔human calibration in ``agreement.py``.

Everything here is development tooling: it is excluded from the wheel (hatch
packages only ``src/aleph``), its dependencies live in the ``evals`` dependency
group, and nothing on the request path imports it — ``MODEL_JUDGE`` in
particular is read here and nowhere else.

Entry point: ``uv run python -m evals`` (or ``just evals``).
"""
