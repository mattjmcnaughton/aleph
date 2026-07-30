"""Unit coverage for the orchestrator's pure Settings→agent mappings (AL-040).

The DB-driven behaviour (claims, prefetch chain, failure semantics) is exercised
in ``tests/integration/test_generation.py`` against real Postgres; these are the
config-free pieces worth pinning cheaply: the caps the service builds from
``Settings`` (the agents never read config — they take caps as run-time deps) and
the exhaustive onboarding-level → agent-level mapping.
"""

from __future__ import annotations

from aleph.agents.outline import Level as AgentLevel
from aleph.config import Settings
from aleph.models import Level
from aleph.services.generation import (
    AGENT_LEVEL,
    _lesson_caps_from,
    _outline_caps_from,
)


def test_outline_caps_built_from_settings() -> None:
    settings = Settings(
        outline_units_target=4,
        max_units=7,
        lessons_per_unit_min=2,
        lessons_per_unit_max=6,
        max_lessons_per_path=25,
    )
    caps = _outline_caps_from(settings)
    assert caps.units_target == 4
    assert caps.max_units == 7
    assert caps.lessons_per_unit_min == 2
    assert caps.lessons_per_unit_max == 6
    assert caps.max_lessons_per_path == 25


def test_lesson_caps_built_from_settings() -> None:
    settings = Settings(read_passage_words_min=150, read_passage_words_max=400)
    caps = _lesson_caps_from(settings)
    assert caps.passage_words_min == 150
    assert caps.passage_words_max == 400


def test_every_onboarding_level_maps_to_an_agent_level() -> None:
    # Exhaustive: a new Level enum member without a mapping would KeyError deep in
    # generation, so pin the contract here.
    assert set(AGENT_LEVEL) == set(Level)
    valid_agent_levels = set(AgentLevel.__args__)  # type: ignore[attr-defined]
    assert set(AGENT_LEVEL.values()) <= valid_agent_levels
