"""Agent eval harness (dev-only, never packaged) — see docs/evals.md.

Runs the agents in ``src/aleph/agents/`` against the curated seed set
(``seed_set.yaml``, TDD §11 / PRD §9) and scores the generations. Everything
here is development tooling: it is excluded from the wheel (hatch packages only
``src/aleph``), its dependencies live in the ``evals`` dependency group, and
nothing on the request path imports it.

Entry point: ``uv run python -m evals`` (or ``just evals``).
"""
