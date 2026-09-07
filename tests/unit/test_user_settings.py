"""Unit tests for learner Settings (CONTEXT.md: Settings / Auto-draft).

The service (``services/user_settings.py``) is driven against an in-memory
fake of its ``SettingsStore`` seam (fakes over mocks): a learner with no row
resolves to the code defaults, a partial patch changes only what it names,
an empty patch writes nothing, and the DTO defaults can never drift from the
service's.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from aleph.dtos.settings import SettingsDTO, SettingsUpdateDTO
from aleph.services.user_settings import (
    DEFAULT_SETTINGS,
    SETTING_NAMES,
    SettingsView,
    load_settings,
    settings_dto,
    update_settings,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class FakeRow:
    """Stands in for the ``UserSettings`` ORM row the store returns — it
    satisfies the service's structural ``SettingsRow`` the same way the ORM
    model does."""

    user_id: uuid.UUID
    auto_draft_flashcards: bool = True


class FakeSettingsStore:
    """An in-memory ``SettingsStore``: one row per user, upsert semantics."""

    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, FakeRow] = {}
        self.upserts: list[Mapping[str, object]] = []

    async def get_for_user(self, user_id: uuid.UUID) -> FakeRow | None:
        return self.rows.get(user_id)

    async def upsert(
        self, *, user_id: uuid.UUID, changes: Mapping[str, object]
    ) -> FakeRow:
        self.upserts.append(changes)
        row = self.rows.setdefault(user_id, FakeRow(user_id=user_id))
        for name, value in changes.items():
            setattr(row, name, value)
        return row


@pytest.mark.anyio
async def test_a_learner_with_no_row_resolves_to_the_defaults() -> None:
    store = FakeSettingsStore()

    view = await load_settings(store, uuid.uuid4())

    assert view == DEFAULT_SETTINGS
    assert view.auto_draft_flashcards is True


@pytest.mark.anyio
async def test_a_patch_changes_only_the_setting_it_names() -> None:
    store = FakeSettingsStore()
    user_id = uuid.uuid4()

    view = await update_settings(store, user_id, {"auto_draft_flashcards": False})

    assert view == SettingsView(auto_draft_flashcards=False)
    assert store.upserts == [{"auto_draft_flashcards": False}]
    # And the change is what a later read sees.
    assert await load_settings(store, user_id) == view


@pytest.mark.anyio
async def test_an_empty_patch_writes_nothing_and_reads_back_the_current_state() -> None:
    store = FakeSettingsStore()
    user_id = uuid.uuid4()
    await update_settings(store, user_id, {"auto_draft_flashcards": False})

    view = await update_settings(store, user_id, {})

    assert view.auto_draft_flashcards is False
    assert len(store.upserts) == 1


@pytest.mark.anyio
async def test_an_unknown_setting_never_reaches_the_store() -> None:
    store = FakeSettingsStore()

    with pytest.raises(ValueError, match="unknown settings"):
        await update_settings(store, uuid.uuid4(), {"dark_mode": True})

    assert store.upserts == []


def test_the_dto_defaults_match_the_service_defaults() -> None:
    """``SettingsDTO`` restates the code defaults so an older client payload
    still reads right; this pins the restatement to the source of truth."""
    assert SettingsDTO().model_dump() == settings_dto(DEFAULT_SETTINGS).model_dump()
    # Every setting the service knows is on the wire, and nothing else.
    assert set(SettingsDTO.model_fields) == SETTING_NAMES
    assert set(SettingsUpdateDTO.model_fields) == SETTING_NAMES


def test_the_update_dto_forbids_unknown_settings() -> None:
    with pytest.raises(ValidationError):
        SettingsUpdateDTO.model_validate({"dark_mode": True})


def test_the_update_dto_tells_unset_apart_from_explicit() -> None:
    """``exclude_unset`` is what makes a partial ``PATCH`` partial."""
    assert SettingsUpdateDTO.model_validate({}).model_dump(exclude_unset=True) == {}
    assert SettingsUpdateDTO.model_validate(
        {"auto_draft_flashcards": False}
    ).model_dump(exclude_unset=True) == {"auto_draft_flashcards": False}
