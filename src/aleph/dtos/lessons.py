"""Lessons API request/response DTOs (AL-051, TDD §6).

The wire contract for ``/api/v1/lessons/{id}`` that the frontend (AL-063)
consumes. Enums reuse the model/domain ``StrEnum``s (``LessonGenerationState``,
``UnlockState``, ``Outcome``) so the frontend's ``lib/api.ts`` fixes one set of
strings — same word, same meaning, prose to schema (CONTEXT.md). All addressing
is by UUID (TDD §6); DTOs are always separate from the ORM models (CLAUDE.md).

**Answer-hiding is a shape invariant (W6, TDD §6).** The keyed
``correct_index`` and the ``explanation`` live **only** inside
:class:`AttemptResultDTO`, and that object is serialized **only after** the
learner has recorded an Attempt (it is ``null`` before). :class:`QuickCheckDTO`
— the pre-Attempt question — carries the ``stem`` and ``options`` and *nothing
else*, so a pre-Attempt ``GET`` payload contains no correct answer anywhere.
Grading is server-side; the client cannot self-grade.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from aleph.domains.grading import (
    REASONING_MAX_CHARS,
    GradingState,
    Outcome,
    ReasoningGrade,
)
from aleph.domains.milestones import MilestoneMetric
from aleph.domains.progression import UnlockState
from aleph.models import LessonGenerationState, LessonVisualKind


class AttemptRequest(BaseModel):
    """``POST /api/v1/lessons/{id}/attempt`` body: the selected option index.

    ``selected_index`` is the 0-based index of the option the learner chose. It
    is graded server-side against the keyed ``correct_index``; an out-of-range
    index simply grades ``incorrect`` (grading's contract, ``domains/grading``),
    so no bound is enforced here — the option count is the agent validator's job.
    """

    selected_index: int
    # The learner's own-words "why", written before the answer is revealed
    # (CONTEXT.md: *Reasoning*). Stored only with the first Attempt, and only
    # when the Quick check asks for one (``asks_reasoning``); ignored otherwise.
    reasoning: str | None = Field(default=None, max_length=REASONING_MAX_CHARS)


class FollowUpAnswerRequest(BaseModel):
    """``POST /api/v1/lessons/{id}/reasoning/follow-up`` body: the one answer."""

    answer: str = Field(min_length=1, max_length=REASONING_MAX_CHARS)


class FollowUpDTO(BaseModel):
    """The grader's one follow-up question and the learner's graded answer.

    ``state`` is ``null`` until the learner answers; ``grade`` and ``feedback``
    are set once that answer is ``graded``. Formative: the Reasoning's own
    ``grade`` stays the first round's.
    """

    question: str
    answer: str | None
    state: GradingState | None
    grade: ReasoningGrade | None
    feedback: str | None


class ReasoningDTO(BaseModel):
    """A Reasoning after its Attempt: the text, its grading, and the key.

    ``state`` is ``grading`` while the grader runs, ``graded`` with ``grade``
    and ``feedback`` set, or ``failed`` when grading did not finish — the
    ``key_points`` are shown either way. ``covered_points`` index
    ``key_points``; ``misconceptions_shown`` are the key's misconception texts
    the reasoning relied on. ``follow_up`` is set when a graded, non-solid
    Reasoning got a follow-up question.
    """

    text: str
    state: GradingState
    grade: ReasoningGrade | None
    feedback: str | None
    key_points: list[str]
    covered_points: list[int]
    misconceptions_shown: list[str]
    follow_up: FollowUpDTO | None


class QuickCheckDTO(BaseModel):
    """The Quick check as shown **before** an Attempt (W6 answer-hiding).

    Deliberately only ``stem`` + ``options`` — no ``correct_index``, no
    ``explanation``. The keyed answer is revealed exclusively through
    :class:`AttemptResultDTO` after the learner attempts, so this object never
    carries it (TDD §6). The options are in their keyed order (the index the
    learner submits is an index into this list).
    """

    stem: str
    options: list[str]
    # Whether to ask for a Reasoning with the Attempt. Says nothing about the
    # answer; the Grading key itself appears only post-Attempt, in ``reasoning``.
    asks_reasoning: bool = False


class AttemptResultDTO(BaseModel):
    """The revealed Quick-check outcome, present only **after** an Attempt.

    Serialized on the lesson detail (``attempt``) once the learner has a
    recorded Attempt, and returned directly from the attempt endpoint. Carries
    the recorded (first-wins) ``selected_index``, the deterministic
    ``outcome`` re-derived from it, and the now-revealed ``correct_index`` +
    ``explanation`` (CONTEXT.md: the Outcome reveals the explanation and lets the
    learner proceed either way). Because it appears only post-Attempt, the
    pre-Attempt payload has no correct answer anywhere (W6).
    """

    selected_index: int
    outcome: Outcome
    correct_index: int
    explanation: str
    reasoning: ReasoningDTO | None = None


class VisualDTO(BaseModel):
    """One interactive **Visual** attached to the Read passage (CONTEXT.md).

    ``id`` is what the passage's ```visual <id> fence names. ``source`` is
    agent-written HTML: untrusted, and the client runs it only inside an
    opaque-origin, no-network sandbox wrapped in kit ``kit_version``
    (docs/tdds/lesson-visuals.md). ``alt`` is the plain-text fallback.
    """

    id: UUID
    kind: LessonVisualKind
    title: str
    alt: str
    source: str
    kit_version: int


class LessonDetailResponse(BaseModel):
    """``GET /api/v1/lessons/{id}`` body — the lesson poll target (§5.4/§6).

    The two orthogonal axes (CONTEXT.md) both surface: ``generation_state`` (the
    stored, effective system axis — a stale ``generating`` reads as ``failed``,
    §5.4) and ``unlock_state`` (the derived learner axis —
    ``locked``/``available``/``complete``, from ``domains/progression``).

    Content fields are populated by state:

    * ``read_passage`` / ``quick_check`` are non-null **only** when
      ``generation_state == generated``; ``visuals`` is empty otherwise.
    * ``attempt`` is non-null **only** after the learner has recorded an Attempt
      — it is the sole carrier of the keyed answer (``correct_index`` /
      ``explanation``), so a pre-Attempt payload hides the answer (W6).
    * ``generation_error`` is a generic, learner-safe message, non-null **only**
      when ``generation_state == failed`` (never raw provider text, §5.5).
    """

    id: UUID
    path_id: UUID
    title: str
    position_in_path: int
    position_in_unit: int
    generation_state: LessonGenerationState
    unlock_state: UnlockState
    read_passage: str | None
    quick_check: QuickCheckDTO | None
    attempt: AttemptResultDTO | None
    generation_error: str | None
    visuals: list[VisualDTO] = Field(default_factory=list)


class GenerateLessonResponse(BaseModel):
    """``202`` body for ``POST /lessons/{id}/generate`` — the id to poll (§5.4)."""

    id: UUID


class PathCompletionDTO(BaseModel):
    """The path this lesson belongs to has no incomplete lesson left.

    Present on a completion response **only** when every lesson on the path is
    complete — its presence *is* the "was that the last one?" answer, which is
    why there is no separate boolean beside it. Carried on the completion
    response rather than left for the client to derive from a refetched path
    detail, because the celebration this feeds has to render on the same frame
    as the tap: inferring it from the invalidated ``GET /paths/{id}`` would put
    a round trip between "Mark complete" and the acknowledgement, and the card
    would show the mid-path copy first and then change its mind.

    ``first_completed_at``/``completed_at`` are the span of the learner's work
    on this path, sent as instants rather than a day count on purpose: a
    **Day** is a calendar day in the *learner's* local timezone (CONTEXT.md).
    The client owns this span's local-day arithmetic; the completion request's
    timezone offset only governs milestone derivation, not these instants.
    """

    lesson_count: int
    first_completed_at: datetime
    completed_at: datetime


class MilestoneDTO(BaseModel):
    """Recognition earned by this completion, never replayed by reads/retries."""

    metric: MilestoneMetric
    days: int


class CompleteLessonResponse(BaseModel):
    """``200`` body for ``POST /lessons/{id}/complete`` — the new unlock state.

    Completion is idempotent: marking the available lesson complete returns
    ``unlock_state == complete``; re-completing an already-complete lesson is a
    no-op that returns the same. A locked lesson never reaches this response —
    it is rejected with ``403`` (only the available lesson may be completed,
    AL-012 / TDD §6).

    ``path_completion`` is idempotent in the same way: it reflects the state of
    the path *after* this call, not what this call changed, so re-completing the
    final lesson answers "yes, the path is complete" a second time rather than
    only for the request that happened to perform the transition. That keeps a
    retried mutation (a flaky first POST, a double tap) from silently
    downgrading the learner's completion screen to the mid-path one.
    """

    id: UUID
    unlock_state: UnlockState
    path_completion: PathCompletionDTO | None = None
    milestones: list[MilestoneDTO] = Field(default_factory=list)
