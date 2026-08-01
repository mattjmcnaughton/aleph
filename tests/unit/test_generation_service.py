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
    _window_prior_triples,
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


def test_window_prior_triples_truncates_to_the_most_recent() -> None:
    # F2/D7: a path longer than the window keeps only the tail, still ascending
    # (the repository already returns ascending order — slicing must preserve it).
    triples = [("Unit 1", f"Lesson {i}", f"passage {i}") for i in range(1, 6)]
    assert _window_prior_triples(triples, window=2) == [
        ("Unit 1", "Lesson 4", "passage 4"),
        ("Unit 1", "Lesson 5", "passage 5"),
    ]


def test_window_prior_triples_unchanged_when_shorter_than_the_window() -> None:
    triples = [("Unit 1", "Lesson 1", "passage 1"), ("Unit 1", "Lesson 2", "passage 2")]
    assert _window_prior_triples(triples, window=30) == triples


def test_window_prior_triples_non_positive_window_is_a_no_op() -> None:
    # Defensive: config default is a positive 30, but a misconfigured 0/negative
    # value must not silently truncate every path to zero continuity.
    triples = [("Unit 1", "Lesson 1", "passage 1")]
    assert _window_prior_triples(triples, window=0) == triples
    assert _window_prior_triples(triples, window=-1) == triples


def test_every_onboarding_level_maps_to_an_agent_level() -> None:
    # Exhaustive: a new Level enum member without a mapping would KeyError deep in
    # generation, so pin the contract here.
    assert set(AGENT_LEVEL) == set(Level)
    valid_agent_levels = set(AgentLevel.__args__)  # type: ignore[attr-defined]
    assert set(AGENT_LEVEL.values()) <= valid_agent_levels
