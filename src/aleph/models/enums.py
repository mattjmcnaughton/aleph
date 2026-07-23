"""Shared enums for the ORM models (CONTEXT.md / TDD §4 state names)."""

from __future__ import annotations

from enum import StrEnum


class Level(StrEnum):
    """A learner's self-assessed starting point for a path (CONTEXT.md)."""

    NEW_TO_IT = "new_to_it"
    SOME_EXPERIENCE = "some_experience"
    WORK_IN_IT = "work_in_it"


class PathStatus(StrEnum):
    """Lifecycle of a path's outline generation (TDD §4).

    ``pending`` -> ``generating`` -> ``ready`` with ``failed`` (retryable) and
    ``refused`` (terminal, safety) branches.
    """

    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"
    REFUSED = "refused"


class LessonGenerationState(StrEnum):
    """Whether a lesson's content exists yet (TDD §4).

    ``ungenerated`` -> ``generating`` -> ``generated`` (terminal, content
    immutable) with ``failed`` as the retryable error branch.
    """

    UNGENERATED = "ungenerated"
    GENERATING = "generating"
    GENERATED = "generated"
    FAILED = "failed"
