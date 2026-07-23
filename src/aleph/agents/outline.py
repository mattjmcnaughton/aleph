"""Outline agent output schemas (TDD §5.1, D12).

The outline agent maps ``(topic, level)`` to a path's units-and-lessons
skeleton, or declines an over-the-boundary topic through a structured refusal.
Per D12 the output is a **union** so a refusal is a first-class result, never
conflated with a failure.

This module defines only the Pydantic output shapes (no bound model, no config,
no services/DB — habagou purity rules). AL-031 assembles the full agent (system
prompt + output validators for §14 caps) around these types; the AL-030 stub
model produces values against them, and AL-052's picker resolves the model.
"""

from __future__ import annotations

from pydantic import BaseModel


class LessonOutline(BaseModel):
    """A single lesson's slot in the outline: a title only (content is generated
    on demand later, per lesson, by the lesson agent)."""

    title: str


class UnitOutline(BaseModel):
    """An ordered grouping of lessons within a path (CONTEXT.md: *Unit*)."""

    title: str
    summary: str
    lessons: list[LessonOutline]


class PathOutline(BaseModel):
    """The units-and-lessons skeleton of a path, generated once at creation.

    Sized per the §14 caps (``MAX_UNITS``, ``LESSONS_PER_UNIT``,
    ``MAX_LESSONS_PER_PATH``); the cap-enforcing output validators live with the
    assembled agent in AL-031.
    """

    units: list[UnitOutline]


class Refusal(BaseModel):
    """The outline agent's structured decline of an over-the-boundary topic.

    A first-class result (D12/W7), phrased as a graceful, non-error explanation —
    never conflated with a generation *failure*.
    """

    message: str


# The outline agent's output type (D12): a valid outline or a structured refusal.
OutlineResult = PathOutline | Refusal
