"""Draft a path from an Idea, and interview the learner to sharpen it.

CONTEXT.md: *Idea*, *Path draft*, *Interview*. Both use cases are synchronous,
stateless model runs on the app-wide model: nothing is stored, and nothing is
created until the learner submits the New path form with a draft's values in it
(Constitution §2).

A provider failure, a timeout, or output that never validated raises
:class:`PathDraftFailedError`, which the route answers as an explicit error:
an empty draft or an empty round of questions would be failure masquerading as
success (Constitution §4).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from aleph.agents.path_draft import (
    InterviewAnswer,
    InterviewDeps,
    InterviewQuestions,
    PathDraft,
    PathDraftDeps,
    build_interview_agent,
    build_interview_prompt,
    build_path_draft_agent,
    build_path_draft_prompt,
)
from aleph.config import Settings
from aleph.config import settings as global_settings
from aleph.services.about_you import get_about_you
from aleph.services.openrouter import resolve_model

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable, Sequence

    from pydantic_ai import Agent
    from pydantic_ai.models import Model
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


class PathDraftFailedError(Exception):
    """The model run failed; there is no draft (or no questions) to show."""


class PathDraftService:
    """Runs the path draft and interview agents for one learner request."""

    def __init__(
        self,
        *,
        resolve_model_fn: Callable[[str], Model] = resolve_model,
        config: Settings = global_settings,
    ) -> None:
        self._resolve_model = resolve_model_fn
        self._config = config

    async def draft(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        idea: str,
        answers: Sequence[InterviewAnswer] = (),
        previous: PathDraft | None = None,
    ) -> PathDraft:
        """A first draft from the idea, or a redraft folding in the answers."""
        deps = PathDraftDeps(
            idea=idea,
            answers=tuple(answers),
            previous=previous,
            about_you=await get_about_you(session, user_id),
        )
        return await self._run(
            build_path_draft_agent(), build_path_draft_prompt(deps), deps, "draft"
        )

    async def questions(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        idea: str,
        draft: PathDraft,
        answers: Sequence[InterviewAnswer] = (),
    ) -> InterviewQuestions:
        """One Interview round about the current draft, asking nothing twice."""
        deps = InterviewDeps(
            idea=idea,
            draft=draft,
            answers=tuple(answers),
            about_you=await get_about_you(session, user_id),
        )
        return await self._run(
            build_interview_agent(), build_interview_prompt(deps), deps, "questions"
        )

    async def _run[D, O](
        self, agent: Agent[D, O], prompt: str, deps: D, kind: str
    ) -> O:
        try:
            async with asyncio.timeout(self._config.generation_timeout_seconds):
                result = await agent.run(
                    prompt,
                    deps=deps,
                    model=self._resolve_model(self._config.model_default),
                )
        except Exception as exc:  # noqa: BLE001 - any provider error is a failed run
            logger.exception("path_draft_failed", kind=kind)
            raise PathDraftFailedError from exc
        return result.output


path_draft_service = PathDraftService()
