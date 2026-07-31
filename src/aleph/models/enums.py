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


class ConversationKind(StrEnum):
    """Which thread a Conversation is (CONTEXT.md: Shaping conversation).

    A path has at most one of each — ``UNIQUE (path_id, kind)`` (Phase 2B TDD
    D3). ``lesson`` is the Phase 2A in-lesson thread, unchanged and the column's
    default so every pre-2B row *is* one; ``shaping`` is the second thread, on
    the path view. The in-lesson rail never shows shaping turns and vice versa,
    which is what lets 2A's surface stay bit-identical (PRD §5.8).
    """

    LESSON = "lesson"
    SHAPING = "shaping"


class PathChangeKind(StrEnum):
    """The edit a Change applied (CONTEXT.md: Addition, Revision).

    Exactly the two operations of the proposal vocabulary (Phase 2B TDD D1) —
    a closed vocabulary is what makes "consent is structural" checkable.
    ``add_lessons`` inserts new lessons (optionally as a new unit);
    ``revise_lesson`` regenerates one unengaged lesson's content. Removal and
    reordering are Phase 4, and get a **declined edit** until then.
    """

    ADD_LESSONS = "add_lessons"
    REVISE_LESSON = "revise_lesson"


class PathChangeStatus(StrEnum):
    """Whether a Change is in force (CONTEXT.md: Change, Undo).

    ``applied`` -> ``undone`` and no further: once the learner engages with
    anything the Change created or revised, undo closes and the row is permanent
    history (Phase 2B TDD D8). There is no ``pending`` — a Change exists only
    because **Apply** committed it.
    """

    APPLIED = "applied"
    UNDONE = "undone"


class MessageRole(StrEnum):
    """Who spoke a Message in a conversation (CONTEXT.md: Tutor, Turn).

    Two members only: the learner and the tutor. There is no system/tool role —
    a turn is exactly one ``learner`` message and the ``tutor`` message it
    produced (Phase 2 TDD §4).
    """

    LEARNER = "learner"
    TUTOR = "tutor"


class MessageSource(StrEnum):
    """How a learner Message was entered (CONTEXT.md: Suggestion).

    Applies to learner rows only (app-enforced, not a CHECK constraint — Phase 2
    TDD §4); ``NULL`` on tutor rows. It is the entry-mix datum behind the §7
    suggestion-usage metric. Selection-to-quote is Phase 2B; when it lands this
    enum gains a ``quote`` member additively.
    """

    TYPED = "typed"
    SUGGESTION = "suggestion"


class LessonGenerationState(StrEnum):
    """Whether a lesson's content exists yet (TDD §4).

    ``ungenerated`` -> ``generating`` -> ``generated`` (terminal, content
    immutable) with ``failed`` as the retryable error branch.
    """

    UNGENERATED = "ungenerated"
    GENERATING = "generating"
    GENERATED = "generated"
    FAILED = "failed"
