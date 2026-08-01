"""The lesson prompt's **revision block** (AL-321, Phase 2B TDD D7).

The one prompt-level change this phase makes to Phase 1's generation machinery,
and deliberately the *only* one: a lesson whose row carries a
``revision_instruction`` generates through the unchanged orchestrator, unchanged
claims and unchanged retries — it simply reads one extra section in its user
prompt. D7's whole argument is that this buys revision with zero orchestration
change, so these tests pin the two properties that keep it true.

1. **An ordinary lesson's prompt is byte-identical to what it was.** A revision
   block that leaked into every generation would re-pitch lessons nobody asked
   to revise.
2. **The instruction rides verbatim.** ``services/stub_model.py`` closes the W18
   loop by looking for its own :data:`SHAPING_REVISION_INSTRUCTION` in the
   assembled prompt (whitespace-collapsed on both sides) and marking the
   regenerated passage when it finds it — so paraphrasing, truncating, or
   interpolating into the instruction would break that link silently.
"""

from __future__ import annotations

from aleph.agents.lesson import (
    LessonDeps,
    LessonRevision,
    PriorPassage,
    build_lesson_prompt,
)
from aleph.agents.outline import LessonOutline, PathOutline, UnitOutline
from aleph.services.stub_model import (
    SHAPING_REVISION_INSTRUCTION,
    build_stub_model,
)

_OUTLINE = PathOutline(
    units=[
        UnitOutline(
            title="Foundations",
            summary="The basics.",
            lessons=[
                LessonOutline(title="Ownership"),
                LessonOutline(title="Borrowing"),
            ],
        )
    ]
)


def _deps(revision: LessonRevision | None = None) -> LessonDeps:
    return LessonDeps(
        topic="Rust ownership",
        level="intermediate",
        outline=_OUTLINE,
        position_in_path=2,
        unit_title="Foundations",
        lesson_title="Borrowing",
        prior_passages=(
            PriorPassage(
                unit_title="Foundations",
                lesson_title="Ownership",
                read_passage="Ownership is Rust's memory model.",
            ),
        ),
        revision=revision,
    )


def _collapse(text: str) -> str:
    return " ".join(text.split())


def test_an_ordinary_lesson_prompt_is_unchanged() -> None:
    """No ``revision`` dep, no revision block — Phase 1's prompt, verbatim."""
    prompt = build_lesson_prompt(_deps())

    assert "revision" not in prompt.casefold()
    assert "previous version" not in prompt.casefold()


def test_the_revision_block_carries_the_instruction_verbatim() -> None:
    """The AL-302 contract: word-for-word, whitespace free (W18's link)."""
    prompt = build_lesson_prompt(
        _deps(LessonRevision(instruction=SHAPING_REVISION_INSTRUCTION))
    )

    assert _collapse(SHAPING_REVISION_INSTRUCTION) in _collapse(prompt)


def test_the_revision_block_carries_the_old_passage_and_the_consistency_rule() -> None:
    """D7: re-pitch, do not re-invent — the old passage is *why* that can work."""
    prompt = build_lesson_prompt(
        _deps(
            LessonRevision(
                instruction="Assume closures are known.",
                previous_passage="Borrowing lets you read without moving.",
            )
        )
    )

    assert "Borrowing lets you read without moving." in prompt
    assert "Assume closures are known." in prompt
    # The consistency posture, stated to the model rather than only in the TDD.
    assert "factual commitment" in prompt.casefold()


def test_a_revision_of_a_never_generated_lesson_omits_the_old_passage() -> None:
    """Revising an ``ungenerated`` lesson is legal — there is just nothing to keep."""
    prompt = build_lesson_prompt(
        _deps(LessonRevision(instruction="Go deeper on lifetimes."))
    )

    assert "Go deeper on lifetimes." in prompt
    assert "the version it replaces" not in prompt.casefold()


def test_the_authoritative_position_still_comes_first() -> None:
    """The AL-032 stub contract: first match wins, so the token stays unique."""
    prompt = build_lesson_prompt(
        _deps(
            LessonRevision(
                instruction="Rewrite it.",
                previous_passage="An older passage about position_in_path=99.",
            )
        )
    )

    assert prompt.startswith("position_in_path=2")


def test_the_stub_recognises_the_assembled_revision_block() -> None:
    """End of the W18 loop, checked here rather than only in Playwright.

    ``services/stub_model`` marks a regenerated passage when it sees its own
    instruction in the prompt. Asserting on the stub's own predicate is what
    makes a re-wrapping of the block below a caught change rather than a silent
    e2e failure three tickets later.
    """
    from aleph.services import stub_model

    prompt = build_lesson_prompt(
        _deps(
            LessonRevision(
                instruction=SHAPING_REVISION_INSTRUCTION,
                previous_passage="The passage this replaces.",
            )
        )
    )

    assert stub_model._revision_requested(prompt) is True
    assert stub_model._revision_requested(build_lesson_prompt(_deps())) is False
    assert build_stub_model() is not None
