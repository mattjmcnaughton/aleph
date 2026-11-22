"""The lesson agent's **Visual** output and its validator (lesson-visuals TDD).

A lesson may carry at most one interactive visual. Its HTML body is stored
beside the Read passage and referenced from it by a ```visual <ref> fence, so
the validator's job is referential integrity plus cheap sanity on the source.
The sandbox is the real defense; these checks exist so a broken or plainly
hostile visual is retried at generation rather than shipped.
"""

from __future__ import annotations

import pytest
from pydantic_ai import ModelRetry

from aleph.agents.lesson import (
    MAX_VISUAL_SOURCE_BYTES,
    SYSTEM_PROMPT,
    LessonCaps,
    LessonContent,
    LessonRevision,
    Visual,
    _revision_block,
    source_calls_kit_ready,
    source_loads_nothing_external,
    validate_lesson_content,
)
from tests.unit._lesson_data import content_dict, passage

_CAPS = LessonCaps()

_SOURCE = """\
<div class="row"><input id="r" type="range" min="1" max="12"></div>
<svg viewBox="0 0 10 10" xmlns="http://www.w3.org/2000/svg"></svg>
<script>document.getElementById("r").oninput = () => {}; kit.ready();</script>
"""


def _visual(**overrides: str) -> dict[str, str]:
    data = {
        "ref": "visual-1",
        "title": "Compound interest over time",
        "alt": "Two bars: simple and compound growth of $1,000 at a chosen rate.",
        "source": _SOURCE,
    }
    data.update(overrides)
    return data


def _with_fences(*refs: str) -> str:
    fences = "\n\n".join(f"```visual {ref}\n```" for ref in refs)
    return f"{passage(240)}\n\n{fences}\n\n{passage(10)}"


def _content(
    *, fences: tuple[str, ...], visuals: list[dict[str, str]]
) -> LessonContent:
    return LessonContent.model_validate(
        content_dict(read_passage=_with_fences(*fences), visuals=visuals)
    )


def test_visuals_default_to_none() -> None:
    content = LessonContent.model_validate(content_dict())

    assert content.visuals == []
    assert validate_lesson_content(_CAPS, content) is content


def test_one_fenced_visual_is_valid() -> None:
    content = _content(fences=("visual-1",), visuals=[_visual()])

    assert validate_lesson_content(_CAPS, content) is content


def test_more_than_one_visual_is_retried() -> None:
    content = _content(
        fences=("visual-1", "visual-2"),
        visuals=[_visual(), _visual(ref="visual-2")],
    )

    with pytest.raises(ModelRetry, match="at most one"):
        validate_lesson_content(_CAPS, content)


def test_a_declared_visual_must_be_fenced() -> None:
    content = _content(fences=(), visuals=[_visual()])

    with pytest.raises(ModelRetry, match="exactly once"):
        validate_lesson_content(_CAPS, content)


def test_a_visual_fenced_twice_is_retried() -> None:
    content = _content(fences=("visual-1", "visual-1"), visuals=[_visual()])

    with pytest.raises(ModelRetry, match="exactly once"):
        validate_lesson_content(_CAPS, content)


def test_a_fence_must_name_a_declared_visual() -> None:
    content = _content(fences=("visual-2",), visuals=[])

    with pytest.raises(ModelRetry, match="visual-2"):
        validate_lesson_content(_CAPS, content)


def test_a_copied_stored_uuid_fence_is_retried() -> None:
    """A Revision's previous passage carries stored UUID fences; the agent must
    write its own placeholder rather than reuse one."""
    uuid_ref = "0b6c1f6e-3d1a-4a8e-9a8b-2f6d3c1e9a10"
    content = _content(fences=(uuid_ref,), visuals=[])

    with pytest.raises(ModelRetry, match="placeholder"):
        validate_lesson_content(_CAPS, content)


def test_a_malformed_fence_is_retried() -> None:
    content = LessonContent.model_validate(
        content_dict(read_passage=f"{passage(240)}\n\n```visual\n```", visuals=[])
    )

    with pytest.raises(ModelRetry, match="fence"):
        validate_lesson_content(_CAPS, content)


def test_a_malformed_ref_on_the_visual_is_retried() -> None:
    content = _content(fences=(), visuals=[_visual(ref="Visual 1")])

    with pytest.raises(ModelRetry, match="ref"):
        validate_lesson_content(_CAPS, content)


@pytest.mark.parametrize("field", ["title", "alt", "source"])
def test_empty_fields_are_retried(field: str) -> None:
    content = _content(fences=("visual-1",), visuals=[_visual(**{field: "  "})])

    with pytest.raises(ModelRetry, match=field):
        validate_lesson_content(_CAPS, content)


def test_an_oversized_source_is_retried() -> None:
    padding = "<!-- pad -->" * (MAX_VISUAL_SOURCE_BYTES // 10)
    content = _content(
        fences=("visual-1",), visuals=[_visual(source=_SOURCE + padding)]
    )

    with pytest.raises(ModelRetry, match="KB"):
        validate_lesson_content(_CAPS, content)


def test_a_source_that_never_calls_kit_ready_is_retried() -> None:
    content = _content(fences=("visual-1",), visuals=[_visual(source="<p>static</p>")])

    with pytest.raises(ModelRetry, match=r"kit\.ready"):
        validate_lesson_content(_CAPS, content)


def test_kit_ready_predicate() -> None:
    assert source_calls_kit_ready("kit.ready()")
    assert source_calls_kit_ready("window.kit.ready ( )")
    assert not source_calls_kit_ready("kit.fail('x')")


@pytest.mark.parametrize(
    "source",
    [
        '<iframe src="about:blank"></iframe>',
        "<object data='x'></object>",
        "<embed src='x'>",
        '<script src="https://cdn.example.com/d3.js"></script>',
        '<img src="//example.com/pixel.gif">',
        "<script>fetch('https://example.com/collect')</script>",
        "<link rel='stylesheet' href='x.css'>",
    ],
)
def test_external_loads_are_rejected(source: str) -> None:
    assert not source_loads_nothing_external(source)
    content = _content(
        fences=("visual-1",),
        visuals=[_visual(source=f"{source}<script>kit.ready()</script>")],
    )
    with pytest.raises(ModelRetry, match="external|network|embed"):
        validate_lesson_content(_CAPS, content)


def test_the_svg_namespace_is_not_an_external_load() -> None:
    assert source_loads_nothing_external(_SOURCE)


def test_the_visual_source_does_not_count_toward_the_word_band() -> None:
    """The band is the Read passage's; the source lives outside it."""
    huge_but_valid = _SOURCE + "<!-- " + ("word " * 1500) + "-->"
    content = _content(fences=("visual-1",), visuals=[_visual(source=huge_but_valid)])

    assert validate_lesson_content(_CAPS, content) is content


def test_visual_schema_fields() -> None:
    assert set(Visual.model_fields) == {"ref", "title", "alt", "source"}


def test_the_system_prompt_teaches_the_visual_rung() -> None:
    for phrase in (
        "```visual visual-1",
        "kit.ready()",
        "include exactly one",
        "leave `visuals` empty",
    ):
        assert phrase in SYSTEM_PROMPT


def test_the_revision_block_says_to_use_fresh_placeholder_refs() -> None:
    block = _revision_block(
        LessonRevision(instruction="slower", previous_passage="old text")
    )

    assert "placeholder" in block
