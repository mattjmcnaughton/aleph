"""Path drafts API: an Idea into a Path draft, and Interview questions.

CONTEXT.md: *Idea*, *Path draft*, *Interview*. Both routes are synchronous and
stateless: they answer with a proposal of New path creation inputs (or one
round of questions) and store nothing. Creating the path stays ``POST /paths``,
reached only when the learner submits the form (Constitution §2).

A failed model run answers ``502 draft_failed`` through the shared envelope,
never an empty ``200`` (Constitution §4).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from aleph.agents.path_draft import InterviewAnswer, PathDraft
from aleph.db import get_session
from aleph.dependencies import get_current_user
from aleph.dtos.path_drafts import (
    CreateDraftQuestionsRequest,
    CreatePathDraftRequest,
    DraftQuestionsResponse,
    InterviewAnswerDTO,
    InterviewQuestionDTO,
    PathDraftDTO,
)
from aleph.errors import error_response
from aleph.models import User  # noqa: TC001 - FastAPI resolves annotations.
from aleph.services.path_draft import PathDraftFailedError, path_draft_service

CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]
router = APIRouter(prefix="/api/v1", tags=["path-drafts"])


def _answers(answers: list[InterviewAnswerDTO]) -> tuple[InterviewAnswer, ...]:
    return tuple(InterviewAnswer(question=a.question, answer=a.answer) for a in answers)


def _draft_in(draft: PathDraftDTO) -> PathDraft:
    return PathDraft.model_validate(draft.model_dump(mode="json"))


def _failed(request: Request, what: str) -> Response:
    return error_response(
        request,
        status_code=502,
        code="draft_failed",
        message=f"Aleph couldn't write {what} just now. Try again.",
    )


@router.post("/path-drafts", response_model=PathDraftDTO)
async def create_path_draft(
    body: CreatePathDraftRequest, request: Request, user: CurrentUser, session: Session
) -> PathDraftDTO | Response:
    """Draft (or redraft) the New path form's inputs. Creates nothing."""
    try:
        draft = await path_draft_service.draft(
            session,
            user.id,
            idea=body.idea,
            answers=_answers(body.answers),
            previous=_draft_in(body.previous) if body.previous else None,
        )
    except PathDraftFailedError:
        return _failed(request, "a draft")
    return PathDraftDTO.model_validate(draft.model_dump())


@router.post("/path-drafts/questions", response_model=DraftQuestionsResponse)
async def create_draft_questions(
    body: CreateDraftQuestionsRequest,
    request: Request,
    user: CurrentUser,
    session: Session,
) -> DraftQuestionsResponse | Response:
    """One round of Interview questions about the draft. Stores nothing."""
    try:
        round_ = await path_draft_service.questions(
            session,
            user.id,
            idea=body.idea,
            draft=_draft_in(body.draft),
            answers=_answers(body.answers),
        )
    except PathDraftFailedError:
        return _failed(request, "questions")
    return DraftQuestionsResponse(
        questions=[
            InterviewQuestionDTO.model_validate(q.model_dump())
            for q in round_.questions
        ]
    )
