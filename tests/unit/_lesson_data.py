"""Shared lesson-content test data builders (AL-032, ponytail-2).

``test_lesson_agent`` and ``test_lesson_validators`` both need an N-word passage
and a valid lesson-content dict with per-field overrides. They are plain helper
functions (not pytest fixtures) on purpose: ``test_lesson_agent`` calls the
content builder inside a module-level ``@pytest.mark.parametrize``, which is
evaluated at *collection* time — before any fixture can resolve — so a factory
function importable by both files is the only form that serves both call sites.

Not collected as tests itself (no ``test_`` / ``Test`` names); imported via
``tests.unit._lesson_data`` since ``tests`` / ``tests.unit`` are packages.
"""

from __future__ import annotations


def passage(words: int) -> str:
    """A passage of exactly ``words`` whitespace-separated tokens."""
    return " ".join(f"w{i}" for i in range(words))


def content_dict(**overrides: object) -> dict[str, object]:
    """A valid lesson-content dict; ``overrides`` patch top-level or QC fields."""
    quick_check: dict[str, object] = {
        "stem": "Which statement best captures this lesson?",
        "options": ["Option A", "Option B", "Option C", "Option D"],
        "correct_index": 0,
        "explanation": "Option A matches the passage; the others distort it.",
    }
    data: dict[str, object] = {
        "read_passage": passage(250),
        "quick_check": quick_check,
    }
    qc_keys = {"stem", "options", "correct_index", "explanation"}
    for key, value in overrides.items():
        if key in qc_keys:
            quick_check[key] = value
        else:
            data[key] = value
    return data
