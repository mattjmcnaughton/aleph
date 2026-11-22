"""The stub's deterministic **Visual** (lesson-visuals TDD; Playwright + dev).

Lesson 1 of every stub path carries one interactive visual — a compound
interest slider — so the browser suite and local dev always have a visual to
render. Later positions carry none, which keeps every other stub contract
(passage shape, word band, sentinels) exactly as it was. ``[force-visual-probe]``
swaps the visual for a hostile sandbox probe the e2e suite asserts is contained.
"""

from __future__ import annotations

import pytest

from aleph.agents.lesson import LessonCaps, validate_lesson_content
from aleph.domains.visuals import visual_fence_refs
from aleph.services.stub_model import (
    FORCE_VISUAL_PROBE,
    _build_lesson,
    clean_topic,
)


def test_lesson_one_carries_one_fenced_valid_visual() -> None:
    lesson = _build_lesson("Rust ownership", 1)

    assert len(lesson.visuals) == 1
    visual = lesson.visuals[0]
    assert visual_fence_refs(lesson.read_passage) == [visual.ref]
    assert "compound" in visual.title.lower()
    assert validate_lesson_content(LessonCaps(), lesson) is lesson


def test_later_lessons_carry_no_visual() -> None:
    lesson = _build_lesson("Rust ownership", 2)

    assert lesson.visuals == []
    assert visual_fence_refs(lesson.read_passage) == []


@pytest.mark.parametrize(
    ("lesson_error", "revised"), [(True, False), (False, True), (True, True)]
)
def test_the_visual_survives_the_other_sentinels(
    lesson_error: bool, revised: bool
) -> None:
    lesson = _build_lesson(
        "a long topic with quite a few words in it",
        1,
        lesson_error=lesson_error,
        revised=revised,
    )

    assert len(lesson.visuals) == 1
    assert validate_lesson_content(LessonCaps(), lesson) is lesson


def test_the_probe_sentinel_swaps_in_the_sandbox_probe() -> None:
    lesson = _build_lesson("Sandbox", 1, visual_probe=True)

    source = lesson.visuals[0].source
    for probe in ("parent.document", "document.cookie", "localStorage", "fetch"):
        assert probe in source
    assert validate_lesson_content(LessonCaps(), lesson) is lesson


def test_the_probe_sentinel_is_stripped_from_the_topic() -> None:
    assert clean_topic(f"Sandbox {FORCE_VISUAL_PROBE}") == "Sandbox"
