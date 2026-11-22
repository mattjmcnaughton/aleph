"""The ```visual fence grammar (CONTEXT.md: **Visual**, docs/tdds/lesson-visuals.md).

A Read passage names an interactive visual by a fenced block whose info string
is ``visual <id>`` and whose body is empty. The agent writes a local placeholder
ref (``visual-1``); persistence rewrites it to the stored row's UUID. These pure
functions are the one grammar the agent validator, the persistence rewrite and
the continuity strip all share.
"""

from __future__ import annotations

from aleph.domains.visuals import (
    is_placeholder_ref,
    rewrite_visual_fences,
    strip_visual_fences,
    visual_fence_refs,
)

_UUID = "0b6c1f6e-3d1a-4a8e-9a8b-2f6d3c1e9a10"


def _passage(*fences: str) -> str:
    body = ["Some prose before."]
    for fence in fences:
        body.append(f"{fence}\n```")
        body.append("More prose.")
    return "\n\n".join(body)


def test_refs_are_read_in_order() -> None:
    passage = _passage("```visual visual-1", "```visual visual-2")

    assert visual_fence_refs(passage) == ["visual-1", "visual-2"]


def test_a_passage_without_visual_fences_has_no_refs() -> None:
    passage = (
        "Prose.\n\n```python\nprint('visual')\n```\n\n```mermaid\nflowchart TD\n```"
    )

    assert visual_fence_refs(passage) == []


def test_a_malformed_info_string_reads_as_none() -> None:
    """Strict: one ref, nothing else. Extra words or no ref are not a fence we
    can resolve, so the validator can reject them by name."""
    passage = _passage(
        "```visual", "```visual a b", "```visual Visual_1", '```visual "x"'
    )

    assert visual_fence_refs(passage) == [None, None, None, None]


def test_a_visual_fence_with_a_body_is_malformed() -> None:
    passage = "Prose.\n\n```visual visual-1\n<div>inline</div>\n```\n\nMore."

    assert visual_fence_refs(passage) == [None]


def test_a_stored_uuid_ref_parses() -> None:
    assert visual_fence_refs(_passage(f"```visual {_UUID}")) == [_UUID]


def test_placeholder_refs_are_short_lowercase_slugs() -> None:
    assert is_placeholder_ref("visual-1")
    assert is_placeholder_ref("compound-growth")
    assert not is_placeholder_ref(_UUID)
    assert not is_placeholder_ref("Visual-1")
    assert not is_placeholder_ref("")
    assert not is_placeholder_ref("-1")


def test_rewrite_replaces_only_mapped_refs() -> None:
    passage = _passage("```visual visual-1")

    rewritten = rewrite_visual_fences(passage, {"visual-1": _UUID})

    assert visual_fence_refs(rewritten) == [_UUID]
    assert rewritten.replace(_UUID, "visual-1") == passage


def test_strip_removes_visual_fences_and_keeps_everything_else() -> None:
    passage = "Before.\n\n```visual visual-1\n```\n\nAfter.\n\n```python\nx = 1\n```"

    stripped = strip_visual_fences(passage)

    assert "visual" not in stripped
    assert "Before." in stripped
    assert "After." in stripped
    assert "```python\nx = 1\n```" in stripped
