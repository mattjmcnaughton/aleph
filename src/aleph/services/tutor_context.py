"""Context assembly for one tutor turn — the named seam (Phase 2 TDD §5.2, D6).

One function, pure reads:

    assemble_lesson_context(session, *, path, lesson_id) -> AssembledContext

It loads the lesson row (Read passage + Quick check + the caller's Attempt if
any), builds the path digest, and turns the stored conversation into pydantic-ai
``message_history``. Everything ``services/tutor.py`` (AL-220) needs to run a
reply, and nothing else: no model, no prompt text, no I/O beyond the five reads
below.

**Why a digest built here rather than ``load_path_detail``.** The Phase 1 read
seams are poll-as-trigger (``services/paths_read.py`` / ``services/lessons_read``
poll the orchestrator, which claims pending lessons for generation and refills
the prefetch window). That side effect has no business in a chat turn: asking
the tutor a question must not start generating lessons. So the digest is
composed here from the unit/lesson titles plus the pure
``domains/progression.derive_unlock_states`` — the same derivation the read
seams use, without the trigger. Every read in this module is a plain ``SELECT``
and nothing is written; the integration suite asserts that as a property, not a
convention.

**The budget shape (§5.2).** The lesson block — Read passage, Quick check, the
Attempt regime — rides in the agent's ``instructions`` (rendered from
:class:`~aleph.agents.tutor.TutorDeps` by
``agents.tutor.render_lesson_context``), which pydantic-ai re-resolves on every
request and orders last, in recency position. ``message_history`` is therefore
*only* the windowed turns, and a 90-turn thread cannot crowd the lesson out: the
two are not competing for the same slot. Input stays ≈5k tokens per turn, flat
for the life of the path.

**The window is a drop, not a summary (D6).** The most recent
``TUTOR_CONTEXT_TURNS`` turns are carried oldest-first; older ones are dropped
outright. Zero machinery, and the summary upgrade — like 2B's
``assemble_path_context`` — lands behind this same signature.

**History is plain learner/tutor text, and that is load-bearing.** Carried turns
are ``ModelRequest[UserPromptPart]`` / ``ModelResponse[TextPart]`` pairs only:

* ``agents/tutor.py`` delivers its whole prompt through ``instructions``
  *because* the history carries no system parts — a ``SystemPromptPart`` here
  would restate a stale Attempt regime on every later turn.
* A prior Tutor check is serialized into its tutor message as compact **text**
  (§5.1), never as a ``ToolCallPart`` — because provider adapters differ in how
  they map a tool call that has no matching tool return, which is exactly the
  shape a carried-in check would have. Text is the one shape every adapter
  renders identically. (It is *not* because a carried tool part would suppress
  this turn's check: both ``agents.tutor``'s ``tutor_check_already_posed`` and
  ``services/stub_model``'s ``_tutor_check_posed`` bound their scan at the
  **last** ``UserPromptPart``, and the current question is appended after this
  history, so everything carried here sits *before* that boundary and is
  already excluded.)

``AGENT_LEVEL`` is **imported** from ``services/generation.py`` rather than
restated: a tutor reply is pitched to the same level the lesson was written for,
and the epic's shared-code rule means a fourth ``Level`` member has to land in
exactly one dict. It lives there because §5.1 put the mapping on the service
that first needed it; services importing services is inside the layering rule
(``routers -> services -> (agents, repositories)``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from aleph.agents.lesson import QuickCheck as QuickCheckPayload
from aleph.agents.tutor import AttemptView, DigestEntry, TutorDeps
from aleph.config import settings
from aleph.domains.grading import Attempt as GradingAttempt
from aleph.domains.grading import grade
from aleph.domains.progression import LessonProgress, derive_unlock_states
from aleph.models import ConversationKind, MessageRole
from aleph.repositories import (
    AttemptRepository,
    ConversationRepository,
    LessonRepository,
    QuickCheckRepository,
    UnitRepository,
)
from aleph.services.generation import AGENT_LEVEL

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from pydantic_ai.messages import ModelMessage
    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.models import Attempt, Lesson, Message, Path


@dataclass(frozen=True)
class AssembledContext:
    """What one tutor reply runs on: the agent's deps plus its carried history.

    The TDD writes this as ``(TutorDeps, message_history)``; it is a frozen
    dataclass rather than a bare tuple for the same reason every other composed
    view in ``services/`` is one — the call site reads ``context.deps`` instead
    of ``context[0]``, and 2B's ``assemble_path_context`` (same signature, same
    return type) can grow a field without breaking an unpacking caller.
    """

    deps: TutorDeps
    message_history: list[ModelMessage]


class LessonContextUnavailableError(LookupError):
    """The lesson cannot ground a turn: it is not on the path, or has no content.

    The router validates both **before** assembly (§5.5 step 1: the lesson
    belongs to the path and has generated content), so reaching this means a
    raced delete or a caller that skipped the check. Raising — rather than
    returning ``None`` — keeps the happy-path signature honest and makes the
    caller bug loud; the turn service maps it to a failed reply, which persists
    nothing (D2).
    """


# --------------------------------------------------------------------------- #
# The pure core: history assembly
# --------------------------------------------------------------------------- #


def render_tutor_check(payload: dict[str, Any]) -> str:
    """A previously posed Tutor check as compact text (TDD §5.1).

    Stem, options with their **zero-based** index (the indexing
    ``pose_tutor_check`` and the check-answer endpoint both use), the correct
    index, and — only once the learner has answered — the index they chose. That
    is what makes "why is that right?" resolve on the next turn without the card
    payload being re-derivable from anywhere else.

    Deliberately *not* included: the check's ``explanation``. §5.1 fixes the
    field list, the tutor's own reply text is carried verbatim immediately above
    this block, and the budget argument for the window is that a carried turn
    stays small.

    Fields are read defensively (``.get``): the payload is JSONB written by this
    app, but a live reply must not die on a row from an older shape.
    """
    options = payload.get("options") or []
    lines = [
        "[Tutor check posed with this reply]",
        f"Stem: {payload.get('stem', '')}",
        "Options (zero-based):",
        *(f"[{index}] {option}" for index, option in enumerate(options)),
        f"Correct option index: {payload.get('correct_index')}",
    ]
    answered_index = payload.get("answered_index")
    if answered_index is not None:
        lines.append(f"Learner's answer index: {answered_index}")
    return "\n".join(lines)


def build_message_history(
    messages: Sequence[Message], *, turns: int | None = None
) -> list[ModelMessage]:
    """The most recent ``turns`` turns of ``messages`` as pydantic-ai history.

    ``messages`` is the whole stored thread in ``position`` order (a path's
    conversation spans its lessons — see ``ConversationRepository.load_thread``).
    It is paired into turns, the last ``turns`` are kept **oldest-first**, and
    the rest are dropped (D6 — nothing is summarized, and nothing stands in for
    a dropped turn).

    ``turns`` defaults to ``TUTOR_CONTEXT_TURNS``. Pairing is strict: a turn is
    one learner message and the tutor message that answered it (D2 writes them
    together, so an unpaired row means a hand-edited or half-deleted thread) and
    anything that does not pair is dropped rather than carried as a half turn a
    model would have to guess at.
    """
    window = settings.tutor_context_turns if turns is None else turns
    if window < 1:
        raise ValueError(f"turns ({window}) must be >= 1 to carry any context.")

    history: list[ModelMessage] = []
    for learner, tutor in _pair_turns(messages)[-window:]:
        history.append(ModelRequest(parts=[UserPromptPart(content=learner.content)]))
        history.append(ModelResponse(parts=[TextPart(content=_tutor_text(tutor))]))
    return history


def _pair_turns(messages: Sequence[Message]) -> list[tuple[Message, Message]]:
    """``messages`` grouped into (learner, tutor) turns; unpaired rows dropped."""
    turns: list[tuple[Message, Message]] = []
    pending: Message | None = None
    for message in messages:
        if message.role is MessageRole.LEARNER:
            pending = message
        elif pending is not None:
            turns.append((pending, message))
            pending = None
    return turns


def _tutor_text(message: Message) -> str:
    """A tutor message's reply text, with any Tutor check it posed appended.

    One ``TextPart`` rather than two: provider adapters differ in how they map a
    multi-part ``ModelResponse``, and "the check rides as text in the message
    that posed it" is the invariant that matters — not which part it lands in.
    """
    if not message.tutor_check:
        return message.content
    return f"{message.content}\n\n{render_tutor_check(message.tutor_check)}"


# --------------------------------------------------------------------------- #
# The seam
# --------------------------------------------------------------------------- #


async def assemble_lesson_context(
    session: AsyncSession, *, path: Path, lesson_id: uuid.UUID
) -> AssembledContext:
    """Everything one in-lesson tutor reply runs on, from five reads.

    ``path`` is the owned :class:`~aleph.models.Path` row the router already
    resolved (``OwnedPath``): it carries the ``topic``/``level`` the reply is
    pitched to and the ``user_id`` whose Attempt counts, so the seam never
    re-reads or re-authorizes it.

    Raises :class:`LessonContextUnavailableError` when ``lesson_id`` is not one
    of the path's lessons or the lesson has no generated content — states the
    router excludes before it gets here (§5.5 step 1).

    Pure reads throughout: nothing is written, nothing is triggered.
    """
    lessons = await LessonRepository(session).list_for_path(path.id)
    lesson = _require_lesson(lessons, lesson_id=lesson_id, path_id=path.id)

    unit_titles = {
        unit.id: unit.title
        for unit in await UnitRepository(session).list_for_path(path.id)
    }
    quick_check = await QuickCheckRepository(session).get_for_lesson(lesson.id)
    if lesson.read_passage is None or quick_check is None:
        raise LessonContextUnavailableError(
            f"lesson {lesson_id} has no generated content to ground a tutor turn"
        )

    attempt = await AttemptRepository(session).get(
        quick_check_id=quick_check.id, user_id=path.user_id
    )
    thread = await ConversationRepository(session).load_thread(
        path.id, kind=ConversationKind.LESSON
    )

    deps = TutorDeps(
        topic=path.topic,
        level=AGENT_LEVEL[path.level],
        unit_title=unit_titles[lesson.unit_id],
        lesson_title=lesson.title,
        position_in_path=lesson.position_in_path,
        read_passage=lesson.read_passage,
        quick_check=QuickCheckPayload(
            stem=quick_check.stem,
            options=list(quick_check.options),
            correct_index=quick_check.correct_index,
            explanation=quick_check.explanation,
        ),
        attempt=_attempt_view(attempt, correct_index=quick_check.correct_index),
        path_digest=_build_digest(lessons, unit_titles),
    )
    return AssembledContext(
        deps=deps,
        message_history=build_message_history(
            [entry.message for entry in thread],
        ),
    )


def _attempt_view(attempt: Attempt | None, *, correct_index: int) -> AttemptView | None:
    """The learner's Attempt as the tutor sees it — or ``None`` if unattempted.

    ``None`` is what selects the pre-Attempt (no-leak) regime in the agent's
    prompt, so "did they attempt?" and "how did it grade?" are one decision made
    in one place.

    The Outcome is re-derived from the recorded ``selected_index`` and never
    read from the ``attempts.is_correct`` denormalization, which is a write-time
    cache that could have drifted from the keyed answer — the same rule
    ``services/lessons_read.py`` follows (AL-012 / ``domains/grading``).
    """
    if attempt is None:
        return None
    return AttemptView(
        selected_index=attempt.selected_index,
        outcome=grade(
            GradingAttempt(selected_index=attempt.selected_index),
            correct_index=correct_index,
        ),
    )


def _require_lesson(
    lessons: Sequence[Lesson], *, lesson_id: uuid.UUID, path_id: uuid.UUID
) -> Lesson:
    """The path's lesson with ``lesson_id`` — picked from the list already read."""
    for lesson in lessons:
        if lesson.id == lesson_id:
            return lesson
    raise LessonContextUnavailableError(f"lesson {lesson_id} is not on path {path_id}")


def _build_digest(
    lessons: Sequence[Lesson], unit_titles: dict[uuid.UUID, str]
) -> tuple[DigestEntry, ...]:
    """The path digest: every lesson's names + derived unlock state, in order.

    ``lessons`` arrives in ``position_in_path`` order and
    :func:`~aleph.domains.progression.derive_unlock_states` returns states
    aligned to its input, so the zip is a straight re-attachment. Names and
    state only — a :class:`~aleph.agents.tutor.DigestEntry` has nowhere to put
    another lesson's body (PRD §5.2).
    """
    states = derive_unlock_states(
        [
            LessonProgress(
                position_in_path=lesson.position_in_path,
                completed_at=lesson.completed_at,
            )
            for lesson in lessons
        ]
    )
    return tuple(
        DigestEntry(
            unit_title=unit_titles[lesson.unit_id],
            lesson_title=lesson.title,
            unlock_state=state,
        )
        for lesson, state in zip(lessons, states, strict=True)
    )
