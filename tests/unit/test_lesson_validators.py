"""Unit tests for the shared lesson-content validator predicates (AL-032).

These predicates live beside the ``LessonContent`` schema in
:mod:`aleph.agents.lesson` (thermo-2) so both the assembled lesson agent (as its
layer-2 output validator) and the eval harness's deterministic pre-filters (TDD
§11) import the *same* code — shared, not duplicated. This file exercises them as
pure functions: each predicate's
boolean contract, the ``LessonCaps`` coherence guard, and the composing
:func:`validate_lesson_content` raising ``ModelRetry`` on each §5.1/§14
violation.
"""

from __future__ import annotations

import pytest
from pydantic_ai import ModelRetry

from aleph.agents.lesson import (
    LessonCaps,
    LessonContent,
    correct_index_in_range,
    count_words,
    has_valid_option_count,
    is_non_empty,
    options_are_distinct,
    passage_within_word_band,
    validate_lesson_content,
)
from tests.unit._lesson_data import content_dict as _content_dict
from tests.unit._lesson_data import passage as _passage

# --- helpers -------------------------------------------------------------------


def _content(**overrides: object) -> LessonContent:
    return LessonContent.model_validate(_content_dict(**overrides))


_CAPS = LessonCaps()


# --- pure predicates -----------------------------------------------------------


def test_count_words() -> None:
    assert count_words("a b c") == 3
    assert count_words("  spaced   out ") == 2
    assert count_words("") == 0


def test_is_non_empty() -> None:
    assert is_non_empty("x")
    assert not is_non_empty("")
    assert not is_non_empty("   ")


def test_has_valid_option_count() -> None:
    assert has_valid_option_count(["a", "b", "c"])
    assert has_valid_option_count(["a", "b", "c", "d"])
    assert not has_valid_option_count(["a", "b"])
    assert not has_valid_option_count(["a", "b", "c", "d", "e"])


def test_correct_index_in_range() -> None:
    assert correct_index_in_range(0, 4)
    assert correct_index_in_range(3, 4)
    assert not correct_index_in_range(4, 4)
    assert not correct_index_in_range(-1, 4)


def test_options_are_distinct() -> None:
    assert options_are_distinct(["A", "B", "C"])
    # Distinctness is case- and whitespace-insensitive (mirrors outline titles).
    assert not options_are_distinct(["A", " a "])
    assert not options_are_distinct(["dup", "dup", "other"])


def test_passage_within_word_band() -> None:
    assert passage_within_word_band(_passage(200))
    assert passage_within_word_band(_passage(500))
    assert passage_within_word_band(_passage(350))
    assert not passage_within_word_band(_passage(199))
    assert not passage_within_word_band(_passage(501))


# --- LessonCaps coherence guard ------------------------------------------------


def test_caps_defaults_mirror_the_section_14_band() -> None:
    caps = LessonCaps()
    assert caps.option_count_min == 3
    assert caps.option_count_max == 4
    assert caps.passage_words_min == 200
    assert caps.passage_words_max == 500


def test_caps_reject_inverted_option_band() -> None:
    with pytest.raises(ValueError, match="option_count"):
        LessonCaps(option_count_min=4, option_count_max=3)


def test_caps_reject_inverted_passage_band() -> None:
    with pytest.raises(ValueError, match="passage_words"):
        LessonCaps(passage_words_min=500, passage_words_max=200)


# --- validate_lesson_content: passes a valid lesson unchanged ------------------


def test_validate_passes_valid_content_unchanged() -> None:
    content = _content()
    assert validate_lesson_content(_CAPS, content) is content


# --- validate_lesson_content: one ModelRetry per §5.1/§14 violation ------------


def test_validate_rejects_too_few_options() -> None:
    with pytest.raises(ModelRetry):
        validate_lesson_content(_CAPS, _content(options=["a", "b"], correct_index=0))


def test_validate_rejects_too_many_options() -> None:
    with pytest.raises(ModelRetry):
        validate_lesson_content(
            _CAPS, _content(options=["a", "b", "c", "d", "e"], correct_index=0)
        )


def test_validate_rejects_correct_index_out_of_range() -> None:
    with pytest.raises(ModelRetry):
        validate_lesson_content(_CAPS, _content(correct_index=9))


def test_validate_rejects_negative_correct_index() -> None:
    with pytest.raises(ModelRetry):
        validate_lesson_content(_CAPS, _content(correct_index=-1))


def test_validate_rejects_duplicate_options() -> None:
    with pytest.raises(ModelRetry):
        validate_lesson_content(
            _CAPS, _content(options=["Same", " same ", "Other"], correct_index=0)
        )


def test_validate_rejects_passage_too_short() -> None:
    with pytest.raises(ModelRetry) as excinfo:
        validate_lesson_content(_CAPS, _content(read_passage=_passage(10)))
    # The message is actionable: it names the band so the model can self-correct.
    assert "word" in str(excinfo.value).lower()


def test_validate_rejects_passage_too_long() -> None:
    with pytest.raises(ModelRetry) as excinfo:
        validate_lesson_content(_CAPS, _content(read_passage=_passage(600)))
    assert "word" in str(excinfo.value).lower()


def test_validate_rejects_empty_stem() -> None:
    with pytest.raises(ModelRetry):
        validate_lesson_content(_CAPS, _content(stem="   "))


def test_validate_rejects_empty_explanation() -> None:
    with pytest.raises(ModelRetry):
        validate_lesson_content(_CAPS, _content(explanation="  "))


def test_validate_respects_custom_caps() -> None:
    # Bands come from the caps object, not constants: a stricter passage band
    # rejects a passage the default band accepts.
    tight = LessonCaps(passage_words_min=200, passage_words_max=210)
    long_content = _content(read_passage=_passage(300))
    with pytest.raises(ModelRetry):
        validate_lesson_content(tight, long_content)
    # The same content passes under the default band.
    assert validate_lesson_content(_CAPS, long_content) is long_content
