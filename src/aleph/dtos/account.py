"""Account-level learner profile DTOs: About you and the Learning summary."""

from __future__ import annotations

import datetime  # noqa: TC003 - pydantic resolves annotations at runtime.
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

from aleph.agents.learning_summary import (  # noqa: TC001 - pydantic needs it
    Suggestion,
    SummaryArea,
)

# CONTEXT.md: *About you*. Bounded like ``TeachingStyleStr`` (2000): it rides
# into every outline, lesson, tutor and shaping prompt, so its length is paid on
# each one.
AboutYouStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]


class AboutYouDTO(BaseModel):
    """``GET``/``PUT /api/v1/about-you``.

    On ``PUT`` the field is required and nullable: ``null`` clears it, like
    ``PUT /paths/{id}/teaching-style``.
    """

    about_you: AboutYouStr | None


class SummaryFactsDTO(BaseModel):
    """Counted from progress on every read; never model-written."""

    lessons_complete: int
    paths: int
    beats: int


class LearningSummaryBodyDTO(BaseModel):
    areas: list[SummaryArea]
    suggestions: list[Suggestion]


class LearningSummaryResponse(BaseModel):
    """``GET``/``POST /api/v1/learning-summary``."""

    state: Literal["too_early", "none", "generating", "ready", "failed"]
    facts: SummaryFactsDTO
    summary: LearningSummaryBodyDTO | None
    written_at: datetime.datetime | None
    min_lessons_complete: int


class PublicProfileSettingsDTO(BaseModel):
    """``GET``/``PUT /api/v1/account/public-profile`` (Public profiles TDD §4).

    On ``PUT``, ``handle`` may be set only while the account has none (D8);
    resending the current one is allowed and changes nothing. ``profile_public``
    needs a Handle.
    """

    handle: Annotated[str, StringConstraints(max_length=64)] | None
    profile_public: bool
    profile_show_activity: bool


class SharedDTO(BaseModel):
    """``PUT /api/v1/paths/{id}/shared`` and ``/beats/{id}/shared``, both ways."""

    shared: bool
