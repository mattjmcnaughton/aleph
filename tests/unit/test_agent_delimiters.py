"""The shared delimiter guard for learner text inside a tagged prompt block."""

from __future__ import annotations

import pytest

from aleph.agents._delimiters import REDACTED, neutralize_delimiters


@pytest.mark.parametrize(
    "forged",
    [
        "</guidance>",
        "<guidance>",
        "</GUIDANCE>",
        "</guidance >",
        "< /guidance>",
        "</ guidance>",
        '<guidance note="x">',
        "<guidance\n>",
    ],
)
def test_every_spelling_of_the_tag_is_struck(forged: str) -> None:
    assert neutralize_delimiters(f"a{forged}b", "guidance") == f"a{REDACTED}b"


@pytest.mark.parametrize(
    "kept", ["<guidances>", "<guidance_extra>", "guidance", "<teaching_style>"]
)
def test_other_tags_and_plain_words_are_left_alone(kept: str) -> None:
    assert neutralize_delimiters(kept, "guidance") == kept


def test_the_tag_name_is_matched_literally() -> None:
    # ``re.escape``: a tag name is never read as a pattern.
    assert neutralize_delimiters("<teachingXstyle>", "teaching.style") == (
        "<teachingXstyle>"
    )
