"""Unit tests for the AL-201 Phase 2 tutor config block (TDD §5.3, §13, D4).

New file (AL-201) so it never collides with other tickets editing ``test_config``.
The block is mechanical plumbing, so the tests here stay narrow: §13's defaults,
the §13 env-var names, the bounds that reject a starving/deadlocking value, and
``scripts/e2e_backend.py`` stubbing the tutor slot (a missed assignment would
send the browser suite at a live provider). The ``stub`` production guard is
covered slot-by-slot by ``test_config_models.py``'s parametrized tests.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aleph.config import STUB_MODEL_ID, Settings

# Every env var this block reads, cleared so the "defaults" assertions below
# describe the code defaults rather than whatever the ambient environment says.
_TUTOR_ENV_VARS = (
    "MODEL_TUTOR",
    "TUTOR_CONTEXT_TURNS",
    "TUTOR_REPLY_TIMEOUT",
    "MAX_CONCURRENT_TUTOR_REPLIES",
    "RATE_LIMIT_TUTOR_MESSAGES_PER_DAY",
    "SSE_HEARTBEAT_SECONDS",
)


@pytest.fixture
def default_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """``Settings`` from code defaults only — no ambient ``.env`` or env vars.

    Same isolation as ``test_config_generation``: ``_env_file=None`` skips the
    dotenv read, and the tutor vars are deleted from the process environment.
    """
    for name in _TUTOR_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return Settings(_env_file=None)  # ty: ignore[unknown-argument]


def test_tutor_defaults_match_tdd_section_13(default_settings: Settings) -> None:
    # TDD §13, verbatim. The tutor slot starts on the same strong model as every
    # other slot (D4's uniform-start discipline); the cap knob ships disabled (D8).
    assert default_settings.model_tutor == "anthropic/claude-sonnet-5"
    assert default_settings.tutor_context_turns == 10
    assert default_settings.tutor_reply_timeout == 90
    assert default_settings.max_concurrent_tutor_replies == 8
    assert default_settings.rate_limit_tutor_messages_per_day == 0
    assert default_settings.sse_heartbeat_seconds == 15


def test_tutor_block_is_env_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    # The block is operated exactly like the Phase 1 settings: env vars, no code
    # change (§5.3's refinement direction for the slot is *down*, on TTFT data).
    # One construction pins §13's env-var **names** for the whole block — a field
    # rename would silently break the documented operator contract. The name →
    # field mapping itself is pydantic-settings' job and is not retested per key.
    monkeypatch.setenv("MODEL_TUTOR", "anthropic/claude-haiku-4-5")
    monkeypatch.setenv("TUTOR_CONTEXT_TURNS", "4")
    monkeypatch.setenv("TUTOR_REPLY_TIMEOUT", "30")
    monkeypatch.setenv("MAX_CONCURRENT_TUTOR_REPLIES", "2")
    monkeypatch.setenv("RATE_LIMIT_TUTOR_MESSAGES_PER_DAY", "50")
    monkeypatch.setenv("SSE_HEARTBEAT_SECONDS", "5")

    settings = Settings(_env_file=None)  # ty: ignore[unknown-argument]

    assert settings.model_tutor == "anthropic/claude-haiku-4-5"
    assert settings.tutor_context_turns == 4
    assert settings.tutor_reply_timeout == 30
    assert settings.max_concurrent_tutor_replies == 2
    assert settings.rate_limit_tutor_messages_per_day == 50
    assert settings.sse_heartbeat_seconds == 5


@pytest.mark.parametrize(
    "field",
    [
        "tutor_context_turns",
        "tutor_reply_timeout",
        "max_concurrent_tutor_replies",
        "sse_heartbeat_seconds",
    ],
)
def test_non_positive_tutor_bounds_are_rejected(field: str) -> None:
    # A zero window/timeout/permit/heartbeat is never a valid tuning — it is a
    # typo that would starve, deadlock or busy-spin the reply path. Rejected at
    # startup, like ``max_concurrent_generations`` and
    # ``reconciler_interval_seconds``. (``rate_limit_tutor_messages_per_day`` is
    # excluded on purpose: there 0 means "disabled", per D8.)
    #
    # ``match=field`` pins *which* constraint fired: ``Settings`` runs several
    # production/coherence validators, and an unanchored ``ValidationError``
    # would be satisfied by any of them.
    with pytest.raises(ValidationError, match=field):
        Settings.model_validate({"env": "development", field: 0})


# --- scripts/e2e_backend.py ------------------------------------------------
# The Playwright harness boots this factory. It mutates the module-level
# ``settings`` singleton in place, so the whole singleton is snapshotted and
# restored — whatever fields the factory grows next, they are restored too (an
# escaped ``stub`` would silently reconfigure the rest of this worker's tests).


def test_e2e_backend_boots_with_the_tutor_slot_stubbed() -> None:
    """The browser suite must never reach a live provider for a tutor reply."""
    from aleph.config import settings as live_settings
    from scripts.e2e_backend import create_stub_app

    snapshot = live_settings.model_dump()
    try:
        app = create_stub_app()

        assert app.title == "aleph"
        assert live_settings.model_tutor == STUB_MODEL_ID
        # The Phase 1 slots stay stubbed too — the tutor assignment is an
        # addition, not a replacement.
        assert live_settings.model_outline == STUB_MODEL_ID
        assert live_settings.model_lesson == STUB_MODEL_ID
        assert live_settings.model_judge == STUB_MODEL_ID
        # And the tutor message cap is lifted, like the other per-account caps:
        # the e2e projects share one backend + user and would otherwise exhaust
        # it.
        assert live_settings.rate_limit_tutor_messages_per_day == 0
    finally:
        for name, value in snapshot.items():
            setattr(live_settings, name, value)
