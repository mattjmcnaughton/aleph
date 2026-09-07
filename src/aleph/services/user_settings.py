"""Learner Settings: the per-account preferences that shape the experience
(CONTEXT.md: Settings / Auto-draft).

Two operations behind one narrow ``Protocol`` so ``tests/unit`` drives them
against an in-memory fake (fakes over mocks): :func:`load_settings` resolves
a learner's effective settings, :func:`update_settings` applies a partial
patch and returns the result.

**Absent means default.** Settings are defined in code — :class:`SettingsView`
carries every setting and its default — and the database stores only what a
learner has actually changed (one ``user_settings`` row, created on their
first change). A learner with no row resolves to :data:`DEFAULT_SETTINGS`, so
introducing a setting is one field with a default here, one column with the
same server default in the model/migration, and one field on the DTO — never a
backfill and never a behaviour change for anyone who has not touched it.

Distinct from feature flags on purpose: a flag is the *operator's* switch
(defined in code, overridden per user only by an admin, a kill switch
mid-incident); a setting is the *learner's* (theirs to change, always
honoured, never resolved through an admin baseline). ``auto_draft_flashcards``
does not gate the flashcards surface — the ``flashcards`` flag does — it only
decides whether drafting starts on its own when a lesson opens (Phase 3 TDD
D5) or waits for the learner to ask.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Protocol

from aleph.dtos.settings import SettingsDTO

if TYPE_CHECKING:
    import uuid
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class SettingsView:
    """A learner's effective settings — every setting, defaults filled in.

    The field names are the wire names *and* the column names: the DTO, the
    view and the row spell each setting once, so a patch's keys pass straight
    through to the repository with no mapping table.
    """

    # Auto-draft: whether Aleph drafts flashcards as a lesson opens (on), or
    # only when the learner asks from the completed lesson (off).
    auto_draft_flashcards: bool = True


DEFAULT_SETTINGS = SettingsView()

SETTING_NAMES: frozenset[str] = frozenset(field.name for field in fields(SettingsView))


class SettingsRow(Protocol):
    """What the service reads off a stored row — one attribute per setting.

    Structural, so :class:`~aleph.models.UserSettings` satisfies it without
    the service importing the ORM, and the unit tests' fake row does too.
    """

    @property
    def auto_draft_flashcards(self) -> bool: ...


class SettingsStore(Protocol):
    """The one read and one write the service needs — satisfied by
    :class:`~aleph.repositories.user_settings.UserSettingsRepository` and by
    the unit tests' in-memory fake."""

    async def get_for_user(self, user_id: uuid.UUID) -> SettingsRow | None: ...

    async def upsert(
        self, *, user_id: uuid.UUID, changes: Mapping[str, object]
    ) -> SettingsRow: ...


def _view_of(row: SettingsRow | None) -> SettingsView:
    if row is None:
        return DEFAULT_SETTINGS
    return SettingsView(auto_draft_flashcards=row.auto_draft_flashcards)


async def load_settings(store: SettingsStore, user_id: uuid.UUID) -> SettingsView:
    """The learner's effective settings: their row, or the defaults."""
    return _view_of(await store.get_for_user(user_id))


async def update_settings(
    store: SettingsStore, user_id: uuid.UUID, changes: Mapping[str, object]
) -> SettingsView:
    """Apply a partial patch (setting name -> new value) and return the result.

    Only the settings named change; the rest keep their stored value or, on a
    first change, their default. An empty patch writes nothing and simply
    returns the current settings, so ``PATCH {}`` is a harmless read. A key
    that is not a setting is a programming error (the DTO forbids extras), so
    it raises rather than reaching the database.
    """
    unknown = set(changes) - SETTING_NAMES
    if unknown:
        raise ValueError(f"unknown settings: {sorted(unknown)}")
    if not changes:
        return await load_settings(store, user_id)
    return _view_of(await store.upsert(user_id=user_id, changes=changes))


def settings_dto(view: SettingsView) -> SettingsDTO:
    """The wire shape of a view — explicit construction, the one chosen mapping
    style in this codebase. Shared by ``routers/v1/settings.py`` and the
    session probe (``routers/auth.py``), so the two payloads cannot drift.
    """
    return SettingsDTO(auto_draft_flashcards=view.auto_draft_flashcards)
