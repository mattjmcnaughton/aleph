"""Live-provider contract test for path generation (TDD §12, AL-003).

One real outline round trip **and** one real lesson round trip against the
configured OpenRouter models — a drift canary for the prompts and the output
schemas (``agents/outline.py``, ``agents/lesson.py``), **not** a quality measure
(quality is the §11 eval harness's job). It is the only test that calls a
provider, so it is quarantined in ``tests/external/``: none of the CI jobs run
it (``just gate`` / ``test-integration`` / ``test-e2e`` target the other
``tests/*`` directories and the Playwright suite), and it is gated behind
``@pytest.mark.external`` — reachable only via ``just test-external``.

**Marker convention for this directory:** every test under ``tests/external/``
is tagged ``@pytest.mark.external`` and must be **keyless-safe** — it skips
cleanly when ``OPENROUTER_API_KEY`` is unset, so ``just test-external`` can be
invoked without credentials (it just skips) and never hangs or spends money by
accident. When a key is present it lifts the model-request guard locally with
``override_allow_model_requests(True)``.

No database and no stubs beyond that guard lift: the outline/lesson agents bind
no model and read caps from injected deps, so the real agents run against the
real model with test-constructed deps — exactly how ``services/generation.py``
drives them, minus persistence.
"""

from __future__ import annotations

import pytest
from pydantic_ai.models import override_allow_model_requests

from aleph.agents.lesson import (
    LessonCaps,
    LessonContent,
    LessonDeps,
    build_lesson_agent,
    build_lesson_prompt,
)
from aleph.agents.outline import (
    OutlineCaps,
    OutlineDeps,
    PathOutline,
    build_outline_agent,
)
from aleph.config import settings
from aleph.services.openrouter import resolve_model

_TOPIC = "Introduction to Rust ownership and borrowing"
_LEVEL = "beginner"


@pytest.mark.external
@pytest.mark.anyio
async def test_live_outline_then_lesson_round_trip() -> None:
    if not settings.openrouter_api_key:
        pytest.skip("OPENROUTER_API_KEY is unset; skipping live provider contract test")

    with override_allow_model_requests(True):
        # --- outline: one live round trip -------------------------------------
        outline_run = await build_outline_agent().run(
            _TOPIC,
            deps=OutlineDeps(level=_LEVEL, caps=OutlineCaps()),
            model=resolve_model(settings.model_outline),
        )
        outline = outline_run.output
        # A structured refusal is a valid outline-agent result, but this topic is
        # squarely admissible — a refusal here means the prompt/boundary drifted.
        assert isinstance(outline, PathOutline), f"unexpected refusal: {outline!r}"
        assert outline.units, "outline came back with no units"
        first_unit = outline.units[0]
        assert first_unit.lessons, "first unit came back with no lessons"

        # --- lesson: one live round trip for position 1 -----------------------
        deps = LessonDeps(
            topic=_TOPIC,
            level=_LEVEL,
            outline=outline,
            position_in_path=1,
            unit_title=first_unit.title,
            lesson_title=first_unit.lessons[0].title,
            caps=LessonCaps(),
        )
        lesson_run = await build_lesson_agent().run(
            build_lesson_prompt(deps),
            deps=deps,
            model=resolve_model(settings.model_lesson),
        )

    # The output schema still binds against the live model: a Read passage plus a
    # single-select Quick check with 3-4 distinct options and an in-range answer.
    lesson = lesson_run.output
    assert isinstance(lesson, LessonContent)
    assert lesson.read_passage.strip()
    quick_check = lesson.quick_check
    assert 3 <= len(quick_check.options) <= 4
    assert len(quick_check.options) == len(set(quick_check.options))
    assert 0 <= quick_check.correct_index < len(quick_check.options)
    assert quick_check.stem.strip()
    assert quick_check.explanation.strip()
