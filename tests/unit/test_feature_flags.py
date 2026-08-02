"""Unit tests for the feature-flag registry, settings parsing, and resolution.

The registry is code-defined (``services/feature_flags.py``, whose module
docstring is the authoritative statement of the resolution order); the database
holds only per-user exceptions. These tests are that order's executable
statement — one test per step of the chain, each named for the step it pins
(AL-203, epic #82 owner amendment 1) — plus the two "stale key" rules that make
deleting a flag a pure code change: settings entries and database rows for
unregistered keys are ignored.

Ported from habagou's ``tests/unit/test_feature_flags.py`` and adapted: aleph's
``authz.is_admin`` takes ``Settings`` explicitly, so the service is constructed
with a config, and the repository seam is a small in-memory fake (fakes over
mocks) rather than a database.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, cast

import pytest

from aleph.config import Settings, settings
from aleph.models import User
from aleph.services import feature_flags
from aleph.services.feature_flags import FeatureFlagService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# The admin class is derived from the email domain (``authz.is_admin``); the
# default ``ADMIN_EMAIL_DOMAINS`` is the sole first-party operator.
ADMIN_EMAIL = "admin@mattjmcnaughton.com"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _user() -> User:
    return User(
        id=uuid.uuid4(),
        issuer="https://issuer.example.test",
        subject="flag-user",
        username="flag-user",
        display_name="Flag User",
        email="learner@example.com",
    )


def _admin_user() -> User:
    return User(
        id=uuid.uuid4(),
        issuer="https://issuer.example.test",
        subject="admin-user",
        username="admin-user",
        display_name="Admin User",
        email=ADMIN_EMAIL,
    )


def _service(repository: object) -> FeatureFlagService:
    """A service whose repository seam is a fake; the session is never touched."""
    service = FeatureFlagService(cast("AsyncSession", object()))
    service.repository = cast("feature_flags.FeatureFlagRepository", repository)
    return service


class StubFeatureFlagRepository:
    def __init__(
        self,
        overrides: dict[str, bool] | None = None,
        counts: dict[str, int] | None = None,
    ) -> None:
        self.overrides = overrides or {}
        self.counts = counts or {}

    async def overrides_for_user(self, *, user_id: uuid.UUID) -> dict[str, bool]:
        return self.overrides

    async def override_counts(self) -> dict[str, int]:
        return self.counts


class ExplodingRepository:
    async def overrides_for_user(self, *, user_id: uuid.UUID) -> dict[str, bool]:
        raise AssertionError("must not query the database for an empty registry")


def test_feature_flag_default_map_parses_and_drops_malformed() -> None:
    parsed = Settings(
        feature_flag_defaults=" alpha:on , beta:OFF ,bad, gamma:maybe , :on ,"
    ).feature_flag_default_map
    assert parsed == {"alpha": True, "beta": False}


def test_feature_flag_default_map_empty_by_default() -> None:
    assert Settings().feature_flag_default_map == {}
    assert Settings(feature_flag_defaults="").feature_flag_default_map == {}


def test_tutor_is_registered_and_launched_on_by_default() -> None:
    """Phase 2's one flag: launched, so it defaults **on** (AL-270).

    It spent Phase 2's build-out at ``False`` (epic #82, amendment 1) — that is
    what shipping dark meant. Now that the tutor is live for every learner, the
    code default is the statement of it, so a clone with no
    ``FEATURE_FLAG_DEFAULTS`` set resolves it on.
    """
    assert feature_flags.FeatureFlag.TUTOR == "tutor"
    assert feature_flags.FLAG_DEFAULTS[feature_flags.FeatureFlag.TUTOR] is True


def test_shaping_is_registered_and_launched_on_by_default() -> None:
    """Phase 2B's one flag, registered and launched the same way (AL-370)."""
    assert feature_flags.FeatureFlag.SHAPING == "shaping"
    assert feature_flags.FLAG_DEFAULTS[feature_flags.FeatureFlag.SHAPING] is True


def test_a_launched_flag_is_still_killable_without_a_code_deploy() -> None:
    """The point of leaving the machinery in place: ``:off`` still outranks.

    A default of ``True`` is a default, not a hardcode — the settings map beats
    it (step 2 of the resolution order), which is what keeps the mid-incident
    kill switch real now that neither flag is dark.
    """
    config = Settings(feature_flag_defaults="tutor:off")

    assert feature_flags.effective_defaults(config)["tutor"] is False
    assert feature_flags.effective_defaults(config)["shaping"] is True


def test_the_registry_is_exactly_the_two_phase_flags() -> None:
    # The whole registry in one assertion: a flag added to the enum but missed by
    # ``FLAG_DEFAULTS`` does not exist as far as resolution is concerned, and
    # would silently resolve off everywhere.
    assert feature_flags.known_flag_keys() == frozenset({"tutor", "shaping"})


def test_effective_defaults_applies_settings_over_code_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(feature_flags, "FLAG_DEFAULTS", {"alpha": False, "beta": True})
    monkeypatch.setattr(settings, "feature_flag_defaults", "alpha:on,unknown:on")

    assert feature_flags.effective_defaults(settings) == {"alpha": True, "beta": True}
    assert feature_flags.known_flag_keys() == frozenset({"alpha", "beta"})


@pytest.mark.anyio
async def test_resolve_for_user_prefers_override_and_drops_stale_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(feature_flags, "FLAG_DEFAULTS", {"alpha": False, "beta": True})
    monkeypatch.setattr(settings, "feature_flag_defaults", "")
    service = _service(
        StubFeatureFlagRepository(overrides={"alpha": True, "removed_flag": True})
    )

    resolved = await service.resolve_for_user(_user())

    assert resolved == {"alpha": True, "beta": True}


@pytest.mark.anyio
async def test_resolve_for_user_admin_gets_admin_default_flags_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(feature_flags, "FLAG_DEFAULTS", {"gamma": False, "beta": False})
    monkeypatch.setattr(feature_flags, "ADMIN_DEFAULT_FLAGS", frozenset({"gamma"}))
    monkeypatch.setattr(settings, "feature_flag_defaults", "")
    service = _service(StubFeatureFlagRepository())

    # Admin baseline forces the admin-default flag on; other flags stay off.
    assert await service.resolve_for_user(_admin_user()) == {
        "gamma": True,
        "beta": False,
    }
    # A non-admin still sees the global default.
    assert await service.resolve_for_user(_user()) == {"gamma": False, "beta": False}


@pytest.mark.anyio
async def test_resolve_for_user_settings_default_on_reaches_learners_and_admins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flipping the global default on (AL-270's launch) reaches everyone.

    Both orders agree here — this pins the launch move, not the precedence; the
    ``:off`` case below is what distinguishes settings-over-admin.
    """
    monkeypatch.setattr(feature_flags, "FLAG_DEFAULTS", {"gamma": False})
    monkeypatch.setattr(feature_flags, "ADMIN_DEFAULT_FLAGS", frozenset({"gamma"}))
    monkeypatch.setattr(settings, "feature_flag_defaults", "gamma:on")
    service = _service(StubFeatureFlagRepository())

    assert await service.resolve_for_user(_user()) == {"gamma": True}
    assert await service.resolve_for_user(_admin_user()) == {"gamma": True}


@pytest.mark.anyio
async def test_resolve_for_user_settings_default_off_silences_admins_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The distinguishing case: a settings entry outranks the admin baseline.

    ``FEATURE_FLAG_DEFAULTS`` is a kill switch, so an explicit ``:off`` turns the
    flag off for the admin class as well — the admin baseline only fills in flags
    the settings map says nothing about. (An admin who wants to keep dogfooding
    takes a per-user override, pinned below.)
    """
    monkeypatch.setattr(feature_flags, "FLAG_DEFAULTS", {"gamma": True})
    monkeypatch.setattr(feature_flags, "ADMIN_DEFAULT_FLAGS", frozenset({"gamma"}))
    monkeypatch.setattr(settings, "feature_flag_defaults", "gamma:off")
    service = _service(StubFeatureFlagRepository())

    assert await service.resolve_for_user(_admin_user()) == {"gamma": False}
    assert await service.resolve_for_user(_user()) == {"gamma": False}


@pytest.mark.anyio
async def test_resolve_for_user_override_beats_a_settings_entry_both_ways(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Top of the chain: an override wins over the settings map, on or off."""
    monkeypatch.setattr(feature_flags, "FLAG_DEFAULTS", {"gamma": False})
    monkeypatch.setattr(feature_flags, "ADMIN_DEFAULT_FLAGS", frozenset({"gamma"}))

    monkeypatch.setattr(settings, "feature_flag_defaults", "gamma:off")
    on_top_of_off = _service(StubFeatureFlagRepository(overrides={"gamma": True}))
    assert await on_top_of_off.resolve_for_user(_user()) == {"gamma": True}
    # The admin kept out by the kill switch dogfoods via an override.
    assert await on_top_of_off.resolve_for_user(_admin_user()) == {"gamma": True}

    monkeypatch.setattr(settings, "feature_flag_defaults", "gamma:on")
    off_on_top_of_on = _service(StubFeatureFlagRepository(overrides={"gamma": False}))
    assert await off_on_top_of_on.resolve_for_user(_user()) == {"gamma": False}
    assert await off_on_top_of_on.resolve_for_user(_admin_user()) == {"gamma": False}


@pytest.mark.anyio
async def test_resolve_for_user_admin_override_beats_admin_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(feature_flags, "FLAG_DEFAULTS", {"gamma": False})
    monkeypatch.setattr(feature_flags, "ADMIN_DEFAULT_FLAGS", frozenset({"gamma"}))
    monkeypatch.setattr(settings, "feature_flag_defaults", "")
    service = _service(StubFeatureFlagRepository(overrides={"gamma": False}))

    # An explicit per-user override wins over the admin baseline.
    assert await service.resolve_for_user(_admin_user()) == {"gamma": False}


@pytest.mark.anyio
async def test_resolve_for_user_empty_registry_skips_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(feature_flags, "FLAG_DEFAULTS", {})
    service = _service(ExplodingRepository())

    assert await service.resolve_for_user(_user()) == {}


@pytest.mark.anyio
async def test_list_flags_sorted_with_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(feature_flags, "FLAG_DEFAULTS", {"beta": True, "alpha": False})
    monkeypatch.setattr(settings, "feature_flag_defaults", "")
    service = _service(StubFeatureFlagRepository(counts={"beta": 3, "stale": 9}))

    listed = await service.list_flags()

    assert [(f.key, f.enabled_default, f.override_count) for f in listed] == [
        ("alpha", False, 0),
        ("beta", True, 3),
    ]
