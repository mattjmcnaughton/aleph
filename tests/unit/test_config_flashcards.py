"""Unit tests for the Phase 3 flashcards config block (TDD §13, D2/D10/D13).

New file so it never collides with other tickets editing ``test_config``, the
``test_config_shaping.py`` / ``test_config_models.py`` precedent. The block is
mechanical plumbing plus one validated shape (the ladder, the overdue/cap
relationship, the drafts band), so the tests here stay narrow: §13's defaults,
the env-var names, the ladder parsing, and every validator's failure mode.
``model_flashcard``'s production stub guard is exercised here directly
(mirroring, not editing, ``test_config_models.py``'s parametrized coverage of
the other slots) since this file may not touch that one.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aleph.config import MODEL_SLOTS, STUB_MODEL_ID, Settings

# Every env var this block reads, cleared so the "defaults" assertions below
# describe the code defaults rather than whatever the ambient environment says.
_FLASHCARD_ENV_VARS = (
    "FLASHCARD_DAILY_CAP",
    "FLASHCARD_OVERDUE_SLOTS",
    "FLASHCARD_LADDER_DAYS",
    "FLASHCARD_DRAFTS_MIN",
    "FLASHCARD_DRAFTS_MAX",
    "FLASHCARD_SECONDS_PER_CARD",
    "FLASHCARD_DRAFTS_PER_DAY",
    "MODEL_FLASHCARD",
)


@pytest.fixture
def default_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """``Settings`` from code defaults only — no ambient ``.env`` or env vars."""
    for name in _FLASHCARD_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return Settings(_env_file=None)  # ty: ignore[unknown-argument]


def test_flashcard_defaults_match_tdd_section_13(default_settings: Settings) -> None:
    assert default_settings.flashcard_daily_cap == 10
    assert default_settings.flashcard_overdue_slots == 7
    assert default_settings.flashcard_ladder_days == "1,3,7,14,30"
    assert default_settings.flashcard_ladder == (1, 3, 7, 14, 30)
    assert default_settings.flashcard_drafts_min == 3
    assert default_settings.flashcard_drafts_max == 5
    assert default_settings.flashcard_seconds_per_card == 25
    assert default_settings.flashcard_drafts_per_day == 50
    assert default_settings.model_flashcard == "anthropic/claude-sonnet-5"


def test_model_flashcard_is_listed_in_model_slots() -> None:
    # This is what puts the slot behind the production stub guard (D13) — a
    # slot present as a field but missing from this tuple would silently
    # escape it.
    assert "model_flashcard" in MODEL_SLOTS


def test_flashcard_block_is_env_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLASHCARD_DAILY_CAP", "20")
    monkeypatch.setenv("FLASHCARD_OVERDUE_SLOTS", "12")
    monkeypatch.setenv("FLASHCARD_LADDER_DAYS", "2, 5 ,9")
    monkeypatch.setenv("FLASHCARD_DRAFTS_MIN", "2")
    monkeypatch.setenv("FLASHCARD_DRAFTS_MAX", "6")
    monkeypatch.setenv("FLASHCARD_SECONDS_PER_CARD", "30")
    monkeypatch.setenv("FLASHCARD_DRAFTS_PER_DAY", "100")
    monkeypatch.setenv("MODEL_FLASHCARD", "anthropic/claude-haiku-4-5")

    settings = Settings(_env_file=None)  # ty: ignore[unknown-argument]

    assert settings.flashcard_daily_cap == 20
    assert settings.flashcard_overdue_slots == 12
    assert settings.flashcard_ladder_days == "2, 5 ,9"
    assert settings.flashcard_ladder == (2, 5, 9)
    assert settings.flashcard_drafts_min == 2
    assert settings.flashcard_drafts_max == 6
    assert settings.flashcard_seconds_per_card == 30
    assert settings.flashcard_drafts_per_day == 100
    assert settings.model_flashcard == "anthropic/claude-haiku-4-5"


def test_ladder_parsing_trims_and_drops_empties() -> None:
    settings = Settings(flashcard_ladder_days=" 1, 3 ,, 7 ")
    assert settings.flashcard_ladder == (1, 3, 7)


# --- overdue_slots <= cap ------------------------------------------------------


def test_overdue_slots_boundary_values_are_allowed() -> None:
    Settings(flashcard_daily_cap=10, flashcard_overdue_slots=0)
    Settings(flashcard_daily_cap=10, flashcard_overdue_slots=10)


@pytest.mark.parametrize("overdue_slots", [-1, 11])
def test_overdue_slots_outside_0_to_cap_is_rejected(overdue_slots: int) -> None:
    with pytest.raises(ValidationError, match="flashcard_overdue_slots"):
        Settings.model_validate(
            {"flashcard_daily_cap": 10, "flashcard_overdue_slots": overdue_slots}
        )


# --- the ladder ------------------------------------------------------------


def test_empty_ladder_is_rejected() -> None:
    with pytest.raises(ValidationError, match="flashcard_ladder_days"):
        Settings.model_validate({"flashcard_ladder_days": ""})


@pytest.mark.parametrize("ladder", ["1,0,7", "1,-3,7", "0"])
def test_non_positive_ladder_entry_is_rejected(ladder: str) -> None:
    with pytest.raises(ValidationError, match="flashcard_ladder_days"):
        Settings.model_validate({"flashcard_ladder_days": ladder})


def test_malformed_ladder_entry_is_rejected() -> None:
    with pytest.raises(ValidationError, match="flashcard_ladder_days"):
        Settings.model_validate({"flashcard_ladder_days": "1,abc,7"})


def test_single_rung_ladder_is_allowed() -> None:
    settings = Settings(flashcard_ladder_days="7")
    assert settings.flashcard_ladder == (7,)


# --- the drafts band ---------------------------------------------------------


@pytest.mark.parametrize(
    ("drafts_min", "drafts_max"),
    [(0, 5), (3, 0), (-1, 5)],
)
def test_non_positive_drafts_bound_is_rejected(
    drafts_min: int, drafts_max: int
) -> None:
    with pytest.raises(ValidationError, match="flashcard_drafts_(min|max)"):
        Settings.model_validate(
            {"flashcard_drafts_min": drafts_min, "flashcard_drafts_max": drafts_max}
        )


def test_drafts_min_greater_than_max_is_rejected() -> None:
    with pytest.raises(ValidationError, match="flashcard_drafts_min"):
        Settings.model_validate({"flashcard_drafts_min": 6, "flashcard_drafts_max": 5})


def test_drafts_min_equal_to_max_is_allowed() -> None:
    settings = Settings(flashcard_drafts_min=4, flashcard_drafts_max=4)
    assert settings.flashcard_drafts_min == settings.flashcard_drafts_max == 4


# --- model_flashcard: production stub guard (mirrors test_config_models.py) ---


def test_stub_allowed_outside_production_for_flashcard_slot() -> None:
    settings = Settings(env="development", model_flashcard="stub")
    assert settings.model_flashcard == "stub"


def test_stub_rejected_in_production_for_flashcard_slot() -> None:
    with pytest.raises(
        ValidationError, match=r"not allowed in production.+model_flashcard"
    ):
        Settings.model_validate({"env": "production", "model_flashcard": "stub"})


def test_non_stub_production_config_is_fine_for_flashcard_slot() -> None:
    settings = Settings(
        env="production",
        model_flashcard="anthropic/claude-sonnet-5",
        session_secret_key="a-real-random-production-secret",
        oidc_issuer="https://tenant.auth0.com",
        oidc_client_id="prod-client",
        oidc_client_secret="prod-secret",
    )
    assert settings.model_flashcard == "anthropic/claude-sonnet-5"


# Sanity: the stub id constant used above is the real one, not a typo'd literal.
def test_stub_model_id_constant_is_stub() -> None:
    assert STUB_MODEL_ID == "stub"


# --- Field bounds: the numbers that must not be zero/negative -----------------
#
# ``flashcard_daily_cap`` and ``flashcard_seconds_per_card`` are not part of the
# ``rate_limit_*``/``DailyRateLimiter`` "0 disables" family — a non-positive
# value is never meaningful for either, so they carry a ``Field`` lower bound
# (the ``streak_activity_window_days: int = Field(default=49, ge=1)`` precedent)
# rather than accepting it silently.


def test_daily_cap_of_zero_is_rejected() -> None:
    # A cap of 0 is a permanently empty queue, not a valid "off" switch — unlike
    # the rate-limiter caps, there is no legitimate reading of 0 here.
    with pytest.raises(ValidationError, match="flashcard_daily_cap"):
        Settings.model_validate({"flashcard_daily_cap": 0})


def test_daily_cap_boundary_value_of_one_is_allowed() -> None:
    settings = Settings(flashcard_daily_cap=1, flashcard_overdue_slots=1)
    assert settings.flashcard_daily_cap == 1


@pytest.mark.parametrize("daily_cap", [-1, -10])
def test_negative_daily_cap_is_rejected(daily_cap: int) -> None:
    with pytest.raises(ValidationError, match="flashcard_daily_cap"):
        Settings.model_validate({"flashcard_daily_cap": daily_cap})


@pytest.mark.parametrize("seconds", [0, -25])
def test_non_positive_seconds_per_card_is_rejected(seconds: int) -> None:
    with pytest.raises(ValidationError, match="flashcard_seconds_per_card"):
        Settings.model_validate({"flashcard_seconds_per_card": seconds})


def test_seconds_per_card_boundary_value_of_one_is_allowed() -> None:
    settings = Settings(flashcard_seconds_per_card=1)
    assert settings.flashcard_seconds_per_card == 1


# --- flashcard_drafts_per_day: deliberately unbounded (the rate-limiter family) -


@pytest.mark.parametrize("drafts_per_day", [0, -1, -50])
def test_drafts_per_day_accepts_the_rate_limiter_familys_disable_values(
    drafts_per_day: int,
) -> None:
    # Unlike flashcard_daily_cap/flashcard_seconds_per_card, this setting joins
    # the DailyRateLimiter family (rate_limit_paths_per_day and siblings), whose
    # own convention is "0 or negative disables the cap" — asserted here so a
    # future Field bound added by analogy with the other two doesn't silently
    # take away drafting's off switch.
    settings = Settings(flashcard_drafts_per_day=drafts_per_day)
    assert settings.flashcard_drafts_per_day == drafts_per_day
