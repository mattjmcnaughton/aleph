"""Lesson agent output schemas (TDD §5.1, §5.2).

The lesson agent produces one lesson's content — a Read passage followed by a
single-select Quick check — with awareness of prior lessons (continuity, D7).
It has no refusal branch: the topic was already admitted at outline time.

This module defines only the Pydantic output shapes (no bound model, no config,
no services/DB — habagou purity rules). AL-032 assembles the full agent (system
prompt + output validators for option counts, size band, etc.) around these
types; the AL-030 stub model produces values against them.
"""

from __future__ import annotations

from pydantic import BaseModel


class QuickCheck(BaseModel):
    """The single-select MCQ ending a lesson (CONTEXT.md: *Quick check*).

    A ``stem``, 3-4 ``options``, the ``correct_index`` into them, and an
    ``explanation``. The count/range/duplication checks live with the assembled
    agent's output validators in AL-032 (shared with the eval pre-filters, §11).
    """

    stem: str
    options: list[str]
    correct_index: int
    explanation: str


class LessonContent(BaseModel):
    """A lesson's generated content: one Read passage + one Quick check.

    Content is immutable once generated (TDD §4). The Read-passage size band
    (``READ_PASSAGE_WORDS``, §14) is enforced by the assembled agent in AL-032.
    """

    read_passage: str
    quick_check: QuickCheck
