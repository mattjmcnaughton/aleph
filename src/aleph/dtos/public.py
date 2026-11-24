"""Public profile DTOs: everything ``/api/v1/public/*`` may return (TDD D3).

Dedicated on purpose. No public route returns a signed-in DTO, so a field added
to one of those later cannot leak here. ``tests/unit/test_public_dtos.py`` pins
the exact key set of every model in this module: adding a field is a deliberate
change to that test.

What these never carry (TDD §4): user ids, emails, ids of unshared items, lesson
content, About you, web search settings, model choices, read state.
"""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from aleph.models import Level


class PublicOwnerDTO(BaseModel):
    display_name: str
    handle: str


class PublicActivityCellDTO(BaseModel):
    date: date
    count: int


class PublicActivityDTO(BaseModel):
    """The Daily streak and the 49-day strip, account-wide (PRD §4.11)."""

    current_streak: int
    best_streak: int
    activity: list[PublicActivityCellDTO]


class PublicPathSummaryDTO(BaseModel):
    id: UUID
    title: str
    level: Level
    done: int
    total: int
    current_streak: int


class PublicBeatSummaryDTO(BaseModel):
    id: UUID
    topic: str
    level: Level
    cadence: Literal["weekly"]
    latest_brief_title: str | None
    brief_count: int


class PublicProfileDTO(BaseModel):
    """``GET /api/v1/public/profiles/{handle}``."""

    display_name: str
    handle: str
    member_since: date
    activity: PublicActivityDTO | None
    paths: list[PublicPathSummaryDTO]
    beats: list[PublicBeatSummaryDTO]


class PublicLessonDTO(BaseModel):
    """A lesson's title and the owner's position. Never its content or id."""

    title: str
    complete: bool
    current: bool


class PublicUnitDTO(BaseModel):
    title: str
    lessons: list[PublicLessonDTO]


class PublicPathDTO(BaseModel):
    """``GET /api/v1/public/profiles/{handle}/paths/{path_id}``.

    ``topic``, ``level``, ``guidance`` and ``teaching_style`` are the creation
    inputs a Copy carries (CONTEXT.md: *Copy*).
    """

    id: UUID
    owner: PublicOwnerDTO
    title: str
    topic: str
    level: Level
    guidance: str | None
    teaching_style: str | None
    done: int
    total: int
    units: list[PublicUnitDTO]


class PublicBriefEntryDTO(BaseModel):
    id: UUID
    number: int
    published_on: date
    title: str
    source_count: int


class PublicBeatDTO(BaseModel):
    """``GET /api/v1/public/profiles/{handle}/beats/{beat_id}?cursor=``.

    ``briefs`` is one page, newest first. ``next_cursor`` is the ``cursor`` for
    the next page, ``None`` on the last one.
    """

    id: UUID
    owner: PublicOwnerDTO
    topic: str
    level: Level
    guidance: str | None
    cadence: Literal["weekly"]
    briefs: list[PublicBriefEntryDTO]
    next_cursor: int | None


class PublicSourceDTO(BaseModel):
    position: int
    publisher: str
    title: str
    published_on: date
    url: str


class PublicBriefDTO(BaseModel):
    """``GET /api/v1/public/profiles/{handle}/beats/{beat_id}/briefs/{brief_id}``."""

    id: UUID
    beat_id: UUID
    beat_topic: str
    owner: PublicOwnerDTO
    number: int
    published_on: date
    title: str
    body_markdown: str
    sources: list[PublicSourceDTO]
