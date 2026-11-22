"""Path draft API DTOs (CONTEXT.md: *Idea*, *Path draft*, *Interview*).

The wire contract for ``/api/v1/path-drafts``. A Path draft carries exactly the
New path form's creation inputs, bounded as ``CreatePathRequest`` bounds them,
except that Guidance and Teaching style may be empty (a draft may propose none).
Nothing here is persisted: the client sends back the idea, the answers so far
and the previous draft on every call.
"""

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from aleph.dtos.paths import TopicStr
from aleph.models import Level

IdeaStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]
QuestionStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
]
AnswerStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]
DraftGuidanceStr = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=4000)
]
DraftTeachingStyleStr = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=2000)
]

MAX_ANSWERS = 20


class PathDraftDTO(BaseModel):
    """A Path draft: values for the New path form, never a created path."""

    topic: TopicStr
    level: Level
    guidance: DraftGuidanceStr = ""
    teaching_style: DraftTeachingStyleStr = ""


class InterviewAnswerDTO(BaseModel):
    """One Interview question the learner answered, and their answer, as text."""

    question: QuestionStr
    answer: AnswerStr


class CreatePathDraftRequest(BaseModel):
    """``POST /api/v1/path-drafts``: draft from an idea, or redraft with answers."""

    idea: IdeaStr
    answers: list[InterviewAnswerDTO] = Field(default=[], max_length=MAX_ANSWERS)
    previous: PathDraftDTO | None = None


class CreateDraftQuestionsRequest(BaseModel):
    """``POST /api/v1/path-drafts/questions``: one Interview round for a draft."""

    idea: IdeaStr
    draft: PathDraftDTO
    answers: list[InterviewAnswerDTO] = Field(default=[], max_length=MAX_ANSWERS)


class InterviewQuestionDTO(BaseModel):
    """One multiple-choice Interview question."""

    prompt: str
    why: str
    options: list[str]
    multi: bool


class DraftQuestionsResponse(BaseModel):
    """``200`` body of ``POST /api/v1/path-drafts/questions``."""

    questions: list[InterviewQuestionDTO]
