"""Context assembly for a tutor turn — the named seam (Phase 2 TDD §5.2, D6).

Two functions, pure reads:

    assemble_lesson_context(session, *, path, lesson_id) -> AssembledContext
    assemble_shaping_context(session, *, path)           -> AssembledContext

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
``assemble_shaping_context``, which landed behind this same signature and
shares the very same window — lands behind it too.

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

--------------------------------------------------------------------------- ..

**The shaping half (Phase 2B TDD §5.2, D2, D9).** ``assemble_shaping_context``
is the seam extension the Phase 2 PRD §6 promised — *same signature, same
return type*, for **shaping scope** instead of lesson scope. It lives in this
module rather than a new one precisely because that promise was about a seam,
not about a file: the window discipline, the "history is plain learner/tutor
text" rule and the pure-reads property above are all restated here by reuse
(:func:`_build_history` is literally shared), not by a parallel implementation.

What differs, and only this:

* **No lesson bodies, ever.** Shaping scope (CONTEXT.md) is topic, level, the
  path digest, each attempted lesson's **Outcome**, and the **Change history**.
  A :class:`~aleph.agents.shaper.ShapingDigestEntry` has nowhere to put a Read
  passage and the seam never reads one, which is what keeps the input ≈4.5k
  tokens flat for the life of the path (§5.2) — smaller than a lesson-scope
  turn, since the biggest block of that one is the passage.
* **Outcomes and the D2 engaged flag** join the digest. Engagement is derived,
  never stored: :func:`aleph.domains.engagement.is_engaged` is the one
  predicate, over the two facts
  ``LessonRepository.list_for_path_with_engagement`` supplies. The Outcome is
  re-graded from the recorded ``selected_index`` (never
  ``attempts.is_correct``), exactly as the lesson seam re-grades its Attempt.
* **The caps are computed here and handed over as data** (§5.1): the agent
  reads no config, so ``first_shapeable_position``, ``lessons_remaining`` and
  ``max_lessons_per_proposal`` arrive on
  :class:`~aleph.agents.shaper.ShapingCaps` like every other dependency.
* **Prior Proposals are not messages**, so they cannot ride the history as
  themselves. They are rendered into the tutor message that made them, in the
  compact text form :func:`~aleph.agents.shaper.render_prior_proposal` owns —
  summary plus resolution state — for the same adapter-portability reason a
  prior Tutor check rides as text. That text form is *never* re-implemented
  here; this module only decides the resolution.
* **Resolution is derived, never stored** (§4): *applied* if a live
  ``path_changes`` row references the proposal message, *undone* if that row is
  undone, *superseded* if a later proposal in the thread was applied first and
  re-validating this one against live state now fails, else *pending*.
  :func:`derive_proposal_resolutions` is that derivation, exported so the
  shaping router's DTO (AL-320) computes it from the same function rather than
  a second one that could disagree.

**What this seam deliberately does not render.** ``agents/shaper.py`` states
the engagement boundary as two ``name=value`` marker lines that
``services/stub_model.py`` parses first-match-wins, so they must occur exactly
once in an assembled request. This module emits neither token of its own
accord: the history it builds carries learner text and tutor text only, and a
unit test pins that. The one untrusted string it *does* place there — a prior
Proposal's ``summary``, model-generated — is struck of those tokens by
``render_prior_proposal``, which is where the striking of every other untrusted
value already lives (AL-310's ``_data_value``). A summary is neutralised the
same way on both rails it reaches the model by, or it would be data in the
change-history block and something more authoritative-looking in the history.

**Every block on this rail is bounded.** The digest by ``MAX_LESSONS_PER_PATH``,
the carried turns by ``TUTOR_CONTEXT_TURNS`` — and the Change history by
:data:`MAX_CHANGE_HISTORY`, which is what actually makes §5.2's ≈4.5k a *flat*
budget rather than one that grows with the number of edits a learner has made.
See that constant for why a Revision in particular would otherwise accumulate
forever.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter, ValidationError
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from aleph.agents.lesson import QuickCheck as QuickCheckPayload
from aleph.agents.shaper import (
    ChangeSummary,
    ShaperDeps,
    ShapingCaps,
    ShapingDigestEntry,
    ShapingOperation,
    proposal_violation,
    render_prior_proposal,
)
from aleph.agents.tutor import AttemptView, DigestEntry, TutorDeps
from aleph.config import settings
from aleph.domains.engagement import (
    LessonEngagement,
    first_shapeable_position,
    is_engaged,
)
from aleph.domains.grading import Attempt as GradingAttempt
from aleph.domains.grading import grade
from aleph.domains.progression import LessonProgress, derive_unlock_states
from aleph.models import (
    ConversationKind,
    MessageRole,
    PathChangeKind,
    PathChangeStatus,
)
from aleph.repositories import (
    AttemptRepository,
    ChangeRepository,
    ConversationRepository,
    LessonRepository,
    QuickCheckRepository,
    UnitRepository,
)
from aleph.services.generation import AGENT_LEVEL

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable, Mapping, Sequence

    from pydantic_ai.messages import ModelMessage
    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.agents.shaper import ChangeStatus, ProposalResolution
    from aleph.models import Attempt, Lesson, Message, Path, PathChange
    from aleph.repositories import LessonAnswer


@dataclass(frozen=True)
class AssembledContext[DepsT: TutorDeps | ShaperDeps]:
    """What one tutor reply runs on: the agent's deps plus its carried history.

    The TDD writes this as ``(deps, message_history)``; it is a frozen dataclass
    rather than a bare tuple for the same reason every other composed view in
    ``services/`` is one — the call site reads ``context.deps`` instead of
    ``context[0]``, and it can grow a field without breaking an unpacking
    caller.

    **Parameterised by the agent's deps type**, so the one return type Phase 2B
    D9 asks for ("``assemble_shaping_context(...) -> AssembledContext``, the
    *same* seam") does not cost the in-lesson caller its precision:
    ``assemble_lesson_context`` returns ``AssembledContext[TutorDeps]`` and
    ``assemble_shaping_context`` returns ``AssembledContext[ShaperDeps]``, and
    neither call site has to narrow a union to reach a field. Nothing about the
    2A path changes at run time — this is a type parameter, not a branch.
    """

    deps: DepsT
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
    return _build_history(messages, turns=turns, tutor_text=_tutor_text)


def _build_history(
    messages: Sequence[Message],
    *,
    turns: int | None,
    tutor_text: Callable[[Message], str],
) -> list[ModelMessage]:
    """The windowing and pairing both scopes share (D6 / Phase 2B D9).

    ``tutor_text`` is the only thing that differs between the two rails: the
    in-lesson thread appends a prior **Tutor check**, the shaping thread a prior
    **Proposal**. Everything else — the window arithmetic, the strict pairing,
    the "plain learner/tutor text, no system or tool parts" part shapes — is one
    implementation on purpose, because a second copy is how the two rails start
    carrying context differently without anyone deciding to.
    """
    window = settings.tutor_context_turns if turns is None else turns
    if window < 1:
        raise ValueError(f"turns ({window}) must be >= 1 to carry any context.")

    history: list[ModelMessage] = []
    for learner, tutor in _pair_turns(messages)[-window:]:
        history.append(ModelRequest(parts=[UserPromptPart(content=learner.content)]))
        history.append(ModelResponse(parts=[TextPart(content=tutor_text(tutor))]))
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
) -> AssembledContext[TutorDeps]:
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


# --------------------------------------------------------------------------- #
# Shaping scope: the pure core (Phase 2B TDD §5.2, D2, D9)
# --------------------------------------------------------------------------- #

# Where a **Change**'s plain-language line lives in its payload. Apply (AL-321)
# carries the Proposal's own ``summary`` into the change payload alongside the
# operations and their inverses (TDD §4), because that sentence is already the
# learner-facing statement of what the edit does — the shaping rail's history
# sheet (§6) and this seam both read it, and re-deriving prose from operations
# in two places is how they start disagreeing. :func:`summarize_changes` still
# derives a line when the key is absent, so a row written by an older shape (or
# by a future apply that forgets) never blanks the history.
CHANGE_SUMMARY_KEY = "summary"
CHANGE_OPERATIONS_KEY = "operations"

# How many Changes the shaping block carries — the newest this many, the rest
# dropped (:func:`summarize_changes`).
#
# **Why there has to be a bound at all.** §5.2's ≈4.5k-token budget is *flat for
# the life of the path*, and every other block on this rail is already held flat
# by something: the digest by ``MAX_LESSONS_PER_PATH``, the carried turns by
# ``TUTOR_CONTEXT_TURNS``. The Change history was the exception. A Revision
# spends no path budget (it keeps its lesson's slot), so a learner who keeps
# re-teaching lessons accrues ``path_changes`` rows without limit and the block
# grows without limit with them — the one place on this rail where a long-lived
# path costs more per turn than a fresh one.
#
# **Why twelve.** §5.2 budgets the Change history at ≈200 tokens. A summary is
# one plain sentence plus its status bracket — call it 15 tokens rendered — so
# twelve lines is that budget spent and roughly nothing over it.
#
# Dropped, never summarized: D6's rule for the turn window, and the same
# argument. Recent Changes are what "you already added those" is about, the
# learner still has the whole record in the history sheet (PRD §5.5), and
# summarizing would put a *second* generated account of the path in front of the
# model to disagree with the first.
#
# A module constant and not a setting, deliberately: a knob earns its place when
# something has to differ per deployment or per learner, and nothing here does.
MAX_CHANGE_HISTORY = 12

# Parses a stored proposal payload's operations back into the agent's models, so
# a pending Proposal can be re-validated against live path state. Built once:
# a ``TypeAdapter`` compiles its validator on construction.
_OPERATIONS_ADAPTER: TypeAdapter[list[ShapingOperation]] = TypeAdapter(
    list[ShapingOperation]
)

# The ORM enum mapped onto the agent's own input vocabulary. ``agents/shaper.py``
# owns ``ChangeStatus`` as a ``Literal`` precisely because importing ``models/``
# there is what the layering forbids, so the translation belongs here, on the
# service that sees both. Exhaustive by construction: a third member of either
# enum breaks this lookup loudly rather than defaulting to "applied".
#
# Not ``change.status.value``, which typechecks and reads shorter: that the two
# vocabularies happen to spell their members with the same strings is a
# coincidence the layering does not promise. The agent's ``ChangeStatus`` is the
# agent's, and re-spelling a stored enum value has to break *here*, at the one
# place that claims to know both, rather than quietly reach the prompt.
_STATUS_TO_CHANGE_STATUS: dict[PathChangeStatus, ChangeStatus] = {
    PathChangeStatus.APPLIED: "applied",
    PathChangeStatus.UNDONE: "undone",
}


def _engagements(
    lessons: Sequence[tuple[Lesson, bool]],
) -> list[LessonEngagement]:
    """The D2 facts for each lesson: position, completion, whether it was tried.

    The boundary contract of :mod:`aleph.domains.engagement` — a service maps
    rows to the pure dataclass, the domain decides. ``has_attempt`` comes from
    ``LessonRepository.list_for_path_with_engagement``'s correlated ``EXISTS``,
    never from "is there an answer in the outcome map": engagement is one
    predicate over one pair of facts (D2), and the Outcome read is a separate
    concern that happens to be correlated.
    """
    return [
        LessonEngagement(
            position_in_path=lesson.position_in_path,
            completed_at=lesson.completed_at,
            has_attempt=has_attempt,
        )
        for lesson, has_attempt in lessons
    ]


def build_shaping_digest(
    lessons: Sequence[tuple[Lesson, bool]],
    *,
    unit_titles: Mapping[uuid.UUID, str],
    answers: Mapping[uuid.UUID, LessonAnswer],
    engagements: Sequence[LessonEngagement] | None = None,
) -> tuple[ShapingDigestEntry, ...]:
    """The shaping digest: names, position, unlock state, engagement, Outcome.

    ``lessons`` is exactly what
    ``LessonRepository.list_for_path_with_engagement`` returns — ``(Lesson,
    has_attempt)`` in ``position_in_path`` order — and
    :func:`~aleph.domains.progression.derive_unlock_states` returns states
    aligned to its input, so the zip is a straight re-attachment (as in
    :func:`_build_digest`).

    Two facts 2A's digest does not carry, and nothing else:

    * ``engaged`` — :func:`~aleph.domains.engagement.is_engaged`, the D2
      predicate, over the row's ``completed_at`` and its Attempt flag. Not
      re-spelled here: apply and undo re-check the *same* function, and three
      spellings is how three call sites start disagreeing.
    * ``outcome`` — the Quick check's result for lessons the learner attempted,
      **re-graded** from the recorded ``selected_index`` against the keyed
      ``correct_index`` rather than read from the ``attempts.is_correct``
      denormalization (AL-012). ``None`` for an unattempted lesson, which is
      what the rendered block prints as "not attempted".

    Never carried: the Read passage or the Quick check body (PRD §5.2). A
    :class:`~aleph.agents.shaper.ShapingDigestEntry` has no field for either,
    which is the structural version of that promise.

    ``engagements`` is :func:`_engagements` over the same ``lessons``, derived
    here when omitted. The seam builds the digest *and* the caps from one path
    read and both need it, so it passes the one list rather than having each
    rebuild it — the parameter exists for that, not for supplying different
    facts, and passing a list that does not describe ``lessons`` is a caller
    bug the ``strict=True`` zip below catches by length only.
    """
    states = derive_unlock_states(
        [
            LessonProgress(
                position_in_path=lesson.position_in_path,
                completed_at=lesson.completed_at,
            )
            for lesson, _has_attempt in lessons
        ]
    )
    entries = []
    for (lesson, _has_attempt), state, engagement in zip(
        lessons,
        states,
        _engagements(lessons) if engagements is None else engagements,
        strict=True,
    ):
        answer = answers.get(lesson.id)
        entries.append(
            ShapingDigestEntry(
                lesson_id=str(lesson.id),
                unit_title=unit_titles[lesson.unit_id],
                lesson_title=lesson.title,
                position_in_path=lesson.position_in_path,
                unlock_state=state,
                engaged=is_engaged(engagement),
                outcome=(
                    None
                    if answer is None
                    else grade(
                        GradingAttempt(selected_index=answer.selected_index),
                        correct_index=answer.correct_index,
                    )
                ),
            )
        )
    return tuple(entries)


def build_shaping_caps(
    lessons: Sequence[tuple[Lesson, bool]],
    *,
    max_lessons_per_path: int | None = None,
    max_lessons_per_proposal: int | None = None,
    engagements: Sequence[LessonEngagement] | None = None,
) -> ShapingCaps:
    """The bounds a Proposal is drafted against, as **data** (§5.1).

    The agent reads no config (its purity rule), so the three numbers it needs
    are computed here and injected:

    * ``first_shapeable_position`` — the D2 boundary, from
      :func:`~aleph.domains.engagement.first_shapeable_position`. Past the end
      of a fully engaged path, and ``1`` on an empty one — both edges the domain
      already owns.
    * ``lessons_remaining`` — how many more lessons ``MAX_LESSONS_PER_PATH``
      allows. Floored at zero: a path already at (or somehow past) the cap has
      room for nothing, and a negative would fail ``ShapingCaps``' own coherence
      check rather than say "no room" (PRD §5.4).
    * ``max_lessons_per_proposal`` — ``MAX_LESSONS_PER_PROPOSAL`` verbatim.

    Both bounds default to the configured settings; the parameters exist so the
    pure arithmetic is testable without touching global config.
    ``engagements`` is :func:`build_shaping_digest`'s, for its reason — the seam
    derives the D2 facts once and hands the same list to both.
    """
    path_cap = (
        settings.max_lessons_per_path
        if max_lessons_per_path is None
        else max_lessons_per_path
    )
    proposal_cap = (
        settings.max_lessons_per_proposal
        if max_lessons_per_proposal is None
        else max_lessons_per_proposal
    )
    return ShapingCaps(
        lessons_remaining=max(0, path_cap - len(lessons)),
        max_lessons_per_proposal=proposal_cap,
        first_shapeable_position=first_shapeable_position(
            _engagements(lessons) if engagements is None else engagements
        ),
    )


def summarize_changes(changes: Sequence[PathChange]) -> tuple[ChangeSummary, ...]:
    """The **Change history** as the shaper sees it: plain line + status.

    ``changes`` arrives newest-first from ``ChangeRepository.list_for_path``,
    and that order is passed through unchanged — ``agents/shaper.py`` renders
    the block "newest first as the seam supplies it", so re-sorting here would
    silently contradict the label the model reads. That order is also what makes
    the :data:`MAX_CHANGE_HISTORY` bound a plain head slice: the kept window is
    the most recent Changes and the dropped ones are the oldest.

    The line is the applied Proposal's own ``summary`` (see
    :data:`CHANGE_SUMMARY_KEY`); a payload without one falls back to a derived
    sentence rather than an empty bullet, because the history is the learner's
    record and a blank entry is worse than a generic one. Read defensively
    (``.get``) for ``render_tutor_check``'s reason: the payload is JSONB written
    by this app, but a live reply must not die on a row from an older shape.
    """
    return tuple(
        ChangeSummary(
            summary=change_summary_text(change),
            status=_STATUS_TO_CHANGE_STATUS[change.status],
        )
        for change in changes[:MAX_CHANGE_HISTORY]
    )


def change_summary_text(change: PathChange) -> str:
    """One Change's plain-language line: the stored summary, or a derived one.

    Public because the **Change history** endpoint (AL-321, §6) renders the same
    line the shaper reads, and "plain-language summary" must mean one thing:
    a learner comparing the history sheet with what the tutor says about their
    path is comparing the same sentence, not two generated accounts of it.
    """
    stored = (change.payload or {}).get(CHANGE_SUMMARY_KEY)
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    return _derived_change_summary(change)


def _derived_change_summary(change: PathChange) -> str:
    """The fallback line, from the change's kind and its operation counts."""
    if change.kind is PathChangeKind.REVISE_LESSON:
        return "Revised a lesson on this path."
    added = 0
    for operation in (change.payload or {}).get(CHANGE_OPERATIONS_KEY) or []:
        if isinstance(operation, dict):
            added += len(operation.get("lessons") or [])
    plural = "" if added == 1 else "s"
    return f"Added {added} lesson{plural} to this path."


def derive_proposal_resolutions(
    messages: Sequence[Message],
    changes: Sequence[PathChange],
    *,
    digest: Sequence[ShapingDigestEntry],
    caps: ShapingCaps,
) -> dict[uuid.UUID, ProposalResolution]:
    """Every Proposal message's resolution state, derived (TDD §4).

    There is no status column to keep consistent, so this is the one derivation
    and it is exported: the shaping conversation DTO (§6, AL-320) reports the
    same ``resolution`` to the card, and computing it twice is how the card and
    the model start disagreeing about whether an edit already landed.

    * **applied / undone** — a ``path_changes`` row references the message. A
      Proposal whose operations span both kinds may have produced more than one
      row, so *any* applied row wins: undo is per-change, and a Proposal with
      one live change has not been wholly undone.
    * **superseded** — no change row, but a *later* Proposal in the thread was
      applied and re-validating this one against live state now fails. That is
      §4's definition verbatim, and it is why the check runs the shared D1
      predicates (through :func:`~aleph.agents.shaper.validate_proposal`)
      instead of a local rule: apply's re-validation (D5) must reach the same
      verdict, or the card would say "superseded" and apply would succeed.
    * **pending** — everything else, including a proposal that still validates
      after a later one was applied (two compatible edits, both offerable).

    ``messages`` is the stored thread in ``position`` order; only tutor rows
    carrying a proposal payload appear in the result.
    """
    statuses: dict[uuid.UUID, set[PathChangeStatus]] = {}
    for change in changes:
        if change.message_id is not None:
            statuses.setdefault(change.message_id, set()).add(change.status)

    applied_ids = {
        message_id
        for message_id, seen in statuses.items()
        if PathChangeStatus.APPLIED in seen
    }
    proposals = [
        message
        for message in messages
        if message.role is MessageRole.TUTOR and message.proposal
    ]
    # "Was any *later* proposal applied?" for every proposal, in one backwards
    # pass: walking the thread newest-first, the answer for a message is simply
    # whether an applied one has been seen already.
    later_applied: dict[uuid.UUID, bool] = {}
    applied_seen = False
    for message in reversed(proposals):
        later_applied[message.id] = applied_seen
        applied_seen = applied_seen or message.id in applied_ids

    resolutions: dict[uuid.UUID, ProposalResolution] = {}
    for message in proposals:
        seen = statuses.get(message.id)
        if seen:
            resolutions[message.id] = (
                "applied" if PathChangeStatus.APPLIED in seen else "undone"
            )
            continue
        superseded = later_applied[message.id] and not _revalidates(
            message.proposal or {}, digest=digest, caps=caps
        )
        resolutions[message.id] = "superseded" if superseded else "pending"
    return resolutions


def parse_proposal_operations(
    payload: Mapping[str, Any],
) -> list[ShapingOperation] | None:
    """A stored Proposal's operations as the agent's own models, or ``None``.

    The one place a persisted payload is turned back into something the shared
    D1 predicates can read. Exported because **apply** (AL-321) must do exactly
    this before re-validating against live state (D5), and re-declaring the
    parse there is how apply and the *superseded* derivation would start
    disagreeing about which payloads are even legible.

    ``None`` — not an exception, and not an empty list — for a payload that will
    not parse: an operation shape this app no longer understands cannot be
    re-validated, so every caller must fail **closed**, and a ``None`` is what
    stops that being confused with "a proposal that proposes nothing".
    """
    try:
        return _OPERATIONS_ADAPTER.validate_python(
            payload.get(CHANGE_OPERATIONS_KEY) or []
        )
    except ValidationError:
        return None


def _revalidates(
    payload: dict[str, Any],
    *,
    digest: Sequence[ShapingDigestEntry],
    caps: ShapingCaps,
) -> bool:
    """True when a stored Proposal payload is still legal against live state.

    Runs the agent's own composed rulebook rather than re-listing predicates, so
    draft time, apply time (D5) and this derivation cannot disagree about what
    "well formed" means. It asks
    :func:`~aleph.agents.shaper.proposal_violation` rather than catching
    ``validate_proposal``'s ``ModelRetry``: the retry is the *model's* channel,
    and a stored row being re-checked is a question with a yes/no answer, not a
    model mid-reply.

    A payload that will not even parse is not legal — an operation shape this
    app no longer understands cannot be re-validated, and failing closed matches
    :func:`~aleph.agents.shaper.revision_targets_unengaged`'s posture on an
    unknown lesson.
    """
    operations = parse_proposal_operations(payload)
    if operations is None:
        return False
    if not operations:
        return False
    summary = payload.get(CHANGE_SUMMARY_KEY)
    return (
        proposal_violation(
            operations,
            summary=summary if isinstance(summary, str) else "",
            digest=digest,
            caps=caps,
        )
        is None
    )


def build_shaping_message_history(
    messages: Sequence[Message],
    *,
    resolutions: Mapping[uuid.UUID, ProposalResolution],
    turns: int | None = None,
) -> list[ModelMessage]:
    """The most recent ``turns`` **shaping** turns as pydantic-ai history (D9).

    The same bounded-window discipline as the in-lesson thread — most recent N
    turns, oldest first, older ones **dropped not summarized** — over the
    shaping-kind thread, and the same strict pairing. ``turns`` defaults to
    ``TUTOR_CONTEXT_TURNS``: §13 reuses the tutor's knob on purpose, because
    there is one notion of "recent conversation" (D11's sibling decision).

    A prior **Proposal** rides in the tutor message that made it, as the compact
    text :func:`~aleph.agents.shaper.render_prior_proposal` owns. A message
    whose resolution is not in ``resolutions`` reads as *pending*, which is the
    honest default: a Proposal with no change row and nothing superseding it is
    exactly that.
    """

    def tutor_text(message: Message) -> str:
        return _shaping_tutor_text(message, resolutions=resolutions)

    return _build_history(messages, turns=turns, tutor_text=tutor_text)


def _shaping_tutor_text(
    message: Message, *, resolutions: Mapping[uuid.UUID, ProposalResolution]
) -> str:
    """A shaping tutor message's text, with any Proposal it made appended.

    One ``TextPart``, for ``_tutor_text``'s reason. A payload without a usable
    ``summary`` appends nothing at all rather than an empty card line: the
    summary *is* the compact form (§5.1), so there is nothing to carry, and a
    live reply must not die on a row from an older shape.
    """
    payload = message.proposal
    if not payload:
        return message.content
    summary = payload.get(CHANGE_SUMMARY_KEY)
    if not isinstance(summary, str) or not summary.strip():
        return message.content
    card = render_prior_proposal(
        summary=summary.strip(),
        resolution=resolutions.get(message.id, "pending"),
    )
    return f"{message.content}\n\n{card}"


# --------------------------------------------------------------------------- #
# Shaping scope: the seam
# --------------------------------------------------------------------------- #


async def assemble_shaping_context(
    session: AsyncSession, *, path: Path
) -> AssembledContext[ShaperDeps]:
    """Everything one shaping reply runs on, from five reads (D9).

    ``path`` is the owned :class:`~aleph.models.Path` row the router already
    resolved (``OwnedPath``): it carries the ``topic``/``level`` the reply is
    pitched to, so the seam never re-reads or re-authorizes it. There is no
    ``lesson_id`` — a shaping conversation is about the path as a whole, which
    is the whole reason it is a second thread (PRD §5.1).

    Unlike the lesson seam there is no ``…ContextUnavailableError``: every path
    with an outline can be talked about, and a path that is not ``ready`` is
    refused by the turn service before assembly (§5.5), not here. An empty path
    assembles fine — the digest simply says so, and the caps put the boundary at
    position 1.

    Pure reads throughout: nothing is written, nothing is triggered. The digest
    is composed from titles plus ``derive_unlock_states`` rather than through
    ``load_path_detail`` for the module docstring's reason — the Phase 1 read
    seams poll-as-trigger, and shaping a path must no more start generating
    lessons than asking a question does.
    """
    lessons = await LessonRepository(session).list_for_path_with_engagement(path.id)
    unit_titles = {
        unit.id: unit.title
        for unit in await UnitRepository(session).list_for_path(path.id)
    }
    answers = await AttemptRepository(session).list_answers_for_path(path.id)
    changes = await ChangeRepository(session).list_for_path(path.id)
    thread = await ConversationRepository(session).load_thread(
        path.id, kind=ConversationKind.SHAPING
    )

    # The D2 facts, derived once: the digest reports them per lesson and the caps
    # reduce them to the boundary, and they must be the same facts in both.
    engagements = _engagements(lessons)
    digest = build_shaping_digest(
        lessons, unit_titles=unit_titles, answers=answers, engagements=engagements
    )
    caps = build_shaping_caps(lessons, engagements=engagements)
    messages = [entry.message for entry in thread]

    return AssembledContext(
        deps=ShaperDeps(
            topic=path.topic,
            level=AGENT_LEVEL[path.level],
            caps=caps,
            digest=digest,
            change_history=summarize_changes(changes),
        ),
        message_history=build_shaping_message_history(
            messages,
            resolutions=derive_proposal_resolutions(
                messages, changes, digest=digest, caps=caps
            ),
        ),
    )
