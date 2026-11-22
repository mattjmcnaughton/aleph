"""The tutor sees a lesson's **Visual** by title and alt text, never its source.

The source is executable, agent-written HTML; the tutor has no use for it and
it would crowd the prompt. Title and alt are what a learner can see and ask
about.
"""

from __future__ import annotations

from aleph.agents.lesson import QuickCheck
from aleph.agents.tutor import TutorDeps, VisualSummary, render_lesson_context

_FENCE = "```visual 0b6c1f6e-3d1a-4a8e-9a8b-2f6d3c1e9a10\n```"


def _deps(*, visuals: tuple[VisualSummary, ...]) -> TutorDeps:
    return TutorDeps(
        topic="Interest",
        level="beginner",
        unit_title="Foundations",
        lesson_title="Compounding",
        position_in_path=1,
        read_passage=f"Compounding earns interest on interest.\n\n{_FENCE}\n\nMore.",
        quick_check=QuickCheck(
            stem="Which grows faster?",
            options=["Simple", "Compound", "Neither"],
            correct_index=1,
            explanation="Compound.",
        ),
        visuals=visuals,
    )


def test_the_visual_title_and_alt_reach_the_tutor() -> None:
    context = render_lesson_context(
        _deps(
            visuals=(VisualSummary(title="Compound interest", alt="Two bars compare."),)
        )
    )

    assert "Compound interest" in context
    assert "Two bars compare." in context


def test_a_lesson_without_a_visual_renders_no_visual_block() -> None:
    assert "<lesson-visual>" not in render_lesson_context(_deps(visuals=()))
