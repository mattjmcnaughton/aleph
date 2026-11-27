"""Reasoning: grade a learner's own-words "why" and show them the result.

A Quick check with a **Grading key** asks, before the answer is revealed, why the
chosen option is right (CONTEXT.md: *Reasoning*). The text arrives with the
Attempt and is stored in the same transaction (``routers/v1/lessons.py``), so
first-wins covers it. Grading runs **after** that commit, inside the request:
the Attempt is durable whether or not the grader answers, and a grader that
errors or times out is recorded as ``failed`` — the learner still gets the
Grading key, never a silent "no feedback" (Constitution §4). A row left
ungraded by a crash reads as failed once it is older than
:data:`GRADING_STALE_AFTER`.

Formative only: nothing here touches progress, unlock state, product events or
the Learning summary. The Outcome of record is still the Attempt's option.
"""

from __future__ import annotations

import asyncio
import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from aleph.agents.reasoning_grader import (
    FollowUp,
    ReasoningAssessment,
    ReasoningDeps,
    build_reasoning_grader_agent,
    build_reasoning_prompt,
)
from aleph.config import settings as global_settings
from aleph.domains.grading import (
    GradingState,
    Outcome,
    ReasoningGrade,
    grade_reasoning,
)
from aleph.repositories import ReasoningRepository
from aleph.services.openrouter import resolve_model

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic_ai.models import Model
    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.config import Settings
    from aleph.models import Attempt, QuickCheck, Reasoning

    ResolveModel = Callable[[str], Model]

logger = structlog.get_logger(__name__)

# One grading call, end to end. The learner is waiting on the reveal for it, so
# it is much shorter than a lesson's generation budget.
GRADING_TIMEOUT_SECONDS = 30.0
# An ungraded row older than this was abandoned (a crash or a deploy mid-call).
GRADING_STALE_AFTER = datetime.timedelta(minutes=2)


@dataclass(frozen=True)
class FollowUpView:
    """The grader's one follow-up question and, once given, the learner's answer."""

    question: str
    answer: str | None
    state: GradingState | None  # None until the learner answers
    grade: ReasoningGrade | None
    feedback: str | None


@dataclass(frozen=True)
class ReasoningView:
    """A Reasoning as the learner sees it after their Attempt."""

    text: str
    state: GradingState
    grade: ReasoningGrade | None
    feedback: str | None
    key_points: tuple[str, ...]
    covered_points: tuple[int, ...]
    misconceptions_shown: tuple[str, ...]
    follow_up: FollowUpView | None


def asks_reasoning(quick_check: QuickCheck, *, enabled: bool) -> bool:
    """Whether this Quick check asks for a Reasoning (flag on and a key exists)."""
    return enabled and quick_check.grading_key is not None


def follow_up_question(reasoning: Reasoning) -> str | None:
    """The follow-up the first-round grading asked, or ``None`` if it asked none."""
    if reasoning.assessment is None:
        return None
    question = ReasoningAssessment.model_validate(
        reasoning.assessment
    ).follow_up_question.strip()
    return question or None


def _state(
    assessment: object,
    *,
    failed: bool,
    since: datetime.datetime,
    now: datetime.datetime,
) -> GradingState:
    if assessment is not None:
        return GradingState.GRADED
    if failed or now - since > GRADING_STALE_AFTER:
        return GradingState.FAILED
    return GradingState.GRADING


def build_reasoning_view(
    reasoning: Reasoning,
    quick_check: QuickCheck,
    *,
    outcome: Outcome,
    now: datetime.datetime,
) -> ReasoningView:
    """Compose the learner-facing view, re-deriving each grade from its facts."""
    key = quick_check.grading_key or {"points": [], "misconceptions": []}
    points = tuple(key["points"])
    misconceptions = key["misconceptions"]
    state = _state(
        reasoning.assessment,
        failed=reasoning.grading_failed,
        since=reasoning.created_at,
        now=now,
    )
    if reasoning.assessment is None:
        return ReasoningView(
            text=reasoning.text,
            state=state,
            grade=None,
            feedback=None,
            key_points=points,
            covered_points=(),
            misconceptions_shown=(),
            follow_up=None,
        )
    first = ReasoningAssessment.model_validate(reasoning.assessment)
    grade = grade_reasoning(
        point_count=len(points),
        covered_points=first.covered_points,
        misconceptions_shown=first.misconceptions_shown,
        outcome=outcome,
    )
    follow_up: FollowUpView | None = None
    if first.follow_up_question.strip():
        follow_up = _follow_up_view(
            reasoning, first.follow_up_question.strip(), len(points), outcome, now
        )
    return ReasoningView(
        text=reasoning.text,
        state=state,
        grade=grade,
        feedback=first.feedback.strip(),
        key_points=points,
        covered_points=tuple(sorted(first.covered_points)),
        misconceptions_shown=tuple(
            misconceptions[index] for index in sorted(first.misconceptions_shown)
        ),
        follow_up=follow_up,
    )


def _follow_up_view(
    reasoning: Reasoning,
    question: str,
    point_count: int,
    outcome: Outcome,
    now: datetime.datetime,
) -> FollowUpView:
    if reasoning.follow_up_answer is None:
        return FollowUpView(
            question=question, answer=None, state=None, grade=None, feedback=None
        )
    state = _state(
        reasoning.follow_up_assessment,
        failed=reasoning.follow_up_grading_failed,
        since=reasoning.updated_at,
        now=now,
    )
    if reasoning.follow_up_assessment is None:
        return FollowUpView(
            question=question,
            answer=reasoning.follow_up_answer,
            state=state,
            grade=None,
            feedback=None,
        )
    second = ReasoningAssessment.model_validate(reasoning.follow_up_assessment)
    return FollowUpView(
        question=question,
        answer=reasoning.follow_up_answer,
        state=state,
        grade=grade_reasoning(
            point_count=point_count,
            covered_points=second.covered_points,
            misconceptions_shown=second.misconceptions_shown,
            outcome=outcome,
        ),
        feedback=second.feedback.strip(),
    )


def _deps(
    quick_check: QuickCheck,
    attempt: Attempt,
    reasoning: Reasoning,
    follow_up: FollowUp | None = None,
) -> ReasoningDeps:
    key = quick_check.grading_key or {"points": [], "misconceptions": []}
    return ReasoningDeps(
        stem=quick_check.stem,
        options=tuple(quick_check.options),
        correct_index=quick_check.correct_index,
        selected_index=attempt.selected_index,
        key_points=tuple(key["points"]),
        misconceptions=tuple(key["misconceptions"]),
        reasoning=reasoning.text,
        follow_up=follow_up,
    )


class ReasoningService:
    """Runs the grader for one round and records the result (first wins)."""

    def __init__(
        self,
        *,
        resolve_model_fn: ResolveModel = resolve_model,
        config: Settings = global_settings,
        timeout_seconds: float = GRADING_TIMEOUT_SECONDS,
    ) -> None:
        self._resolve_model = resolve_model_fn
        self._config = config
        self._timeout = timeout_seconds

    async def _assess(self, deps: ReasoningDeps) -> ReasoningAssessment | None:
        agent = build_reasoning_grader_agent()
        try:
            async with asyncio.timeout(self._timeout):
                result = await agent.run(
                    build_reasoning_prompt(deps),
                    deps=deps,
                    model=self._resolve_model(self._config.model_default),
                )
        except Exception:  # noqa: BLE001 - any grader error is a failed grading
            logger.exception(
                "reasoning_grading_failed", follow_up=deps.follow_up is not None
            )
            return None
        return result.output

    async def grade(
        self,
        session: AsyncSession,
        *,
        quick_check: QuickCheck,
        attempt: Attempt,
        reasoning: Reasoning,
    ) -> None:
        """Grade the first round and commit the result (or the failure)."""
        assessment = await self._assess(_deps(quick_check, attempt, reasoning))
        await ReasoningRepository(session).record_assessment(
            reasoning.id, assessment.model_dump() if assessment else None
        )
        await session.commit()

    async def answer_follow_up(
        self,
        session: AsyncSession,
        *,
        quick_check: QuickCheck,
        attempt: Attempt,
        reasoning: Reasoning,
        question: str,
        answer: str,
    ) -> None:
        """Accept the learner's one follow-up answer and grade it.

        A second answer (a retry or a double tap) is ignored: only the request
        that stored the answer runs the grader.
        """
        repo = ReasoningRepository(session)
        claimed = await repo.claim_follow_up(reasoning.id, answer)
        await session.commit()
        if not claimed:
            return
        deps = _deps(
            quick_check,
            attempt,
            reasoning,
            FollowUp(question=question, answer=answer),
        )
        assessment = await self._assess(deps)
        await repo.record_follow_up_assessment(
            reasoning.id, assessment.model_dump() if assessment else None
        )
        await session.commit()


reasoning_service = ReasoningService()
