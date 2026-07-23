"""Config invariants for the generation timings (TDD §5.4 / §14).

``GENERATION_STALE_AFTER`` must exceed ``GENERATION_TIMEOUT`` (plus overhead):
otherwise a healthy-but-slow generation gets double-claimed while it is still
running. TDD §5.4 lists this as a *tested* invariant, not a comment — so it is
enforced by a pydantic validator and pinned here.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from aleph.config import Settings

if TYPE_CHECKING:
    from collections.abc import Iterator

_TIMING_ENV_VARS = (
    "GENERATION_TIMEOUT_SECONDS",
    "GENERATION_STALE_AFTER_SECONDS",
)


@pytest.fixture
def default_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """A ``Settings`` built from code defaults only — no ambient ``.env`` or env
    vars, which would otherwise flip these "defaults" assertions (an untracked
    ``.env`` and CI env are both real risks). ``_env_file=None`` skips the dotenv
    read; deleting the timing vars isolates the process environment."""
    for name in _TIMING_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield Settings(_env_file=None)  # ty: ignore[unknown-argument]


def test_defaults_satisfy_stale_after_exceeds_timeout(
    default_settings: Settings,
) -> None:
    assert (
        default_settings.generation_stale_after_seconds
        > default_settings.generation_timeout_seconds
    )


def test_default_timings_match_tdd_section_14(default_settings: Settings) -> None:
    # TDD §14: GENERATION_TIMEOUT 60s, GENERATION_STALE_AFTER 3 min.
    assert default_settings.generation_timeout_seconds == 60
    assert default_settings.generation_stale_after_seconds == 180


def test_timings_expose_timedeltas(default_settings: Settings) -> None:
    assert default_settings.generation_timeout == datetime.timedelta(seconds=60)
    assert default_settings.generation_stale_after == datetime.timedelta(minutes=3)


def test_stale_after_equal_to_timeout_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,  # ty: ignore[unknown-argument]
            generation_timeout_seconds=180,
            generation_stale_after_seconds=180,
        )


def test_stale_after_below_timeout_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,  # ty: ignore[unknown-argument]
            generation_timeout_seconds=90,
            generation_stale_after_seconds=60,
        )
