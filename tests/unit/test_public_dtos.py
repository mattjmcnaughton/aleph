"""Pin the exact key set of every public DTO (Public profiles TDD D3, SR 3).

A public response is a published projection (Constitution §1): it returns only
fields named for publication. Adding a field to any of these models must be a
deliberate edit to this test, reviewed as a publication decision.
"""

from __future__ import annotations

import inspect

from pydantic import BaseModel

from aleph.dtos import public

EXPECTED: dict[str, set[str]] = {
    "PublicOwnerDTO": {"display_name", "handle"},
    "PublicActivityCellDTO": {"date", "count"},
    "PublicActivityDTO": {"current_streak", "best_streak", "activity"},
    "PublicPathSummaryDTO": {"id", "title", "level", "done", "total", "current_streak"},
    "PublicBeatSummaryDTO": {
        "id",
        "topic",
        "level",
        "cadence",
        "latest_brief_title",
        "brief_count",
    },
    "PublicProfileDTO": {
        "display_name",
        "handle",
        "member_since",
        "activity",
        "paths",
        "beats",
    },
    "PublicLessonDTO": {"title", "complete", "current"},
    "PublicUnitDTO": {"title", "lessons"},
    "PublicPathDTO": {
        "id",
        "owner",
        "title",
        "topic",
        "level",
        "guidance",
        "teaching_style",
        "done",
        "total",
        "units",
    },
    "PublicBriefEntryDTO": {"id", "number", "published_on", "title", "source_count"},
    "PublicBeatDTO": {
        "id",
        "owner",
        "topic",
        "level",
        "guidance",
        "cadence",
        "briefs",
        "next_cursor",
    },
    "PublicSourceDTO": {"position", "publisher", "title", "published_on", "url"},
    "PublicBriefDTO": {
        "id",
        "beat_id",
        "beat_topic",
        "owner",
        "number",
        "published_on",
        "title",
        "body_markdown",
        "sources",
    },
}


def _models() -> dict[str, type[BaseModel]]:
    return {
        name: obj
        for name, obj in vars(public).items()
        if inspect.isclass(obj)
        and issubclass(obj, BaseModel)
        and obj.__module__ == public.__name__
    }


def test_every_public_dto_is_pinned() -> None:
    assert set(_models()) == set(EXPECTED)


def test_public_dto_key_sets_are_exact() -> None:
    for name, model in _models().items():
        assert set(model.model_fields) == EXPECTED[name], name


def test_no_public_dto_names_an_account_field() -> None:
    forbidden = {"user_id", "email", "username", "about_you", "read_at"}
    for name, model in _models().items():
        assert not forbidden & set(model.model_fields), name
