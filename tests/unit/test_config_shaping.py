"""Unit tests for the AL-301 Phase 2B shaping config block (TDD §5.3, §13, D10).

New file (AL-301) so it never collides with other tickets editing ``test_config``.
The block is mechanical plumbing, so the tests here stay narrow: §13's defaults,
the §13 env-var names, the bound that rejects a Proposal cap no Proposal could
satisfy, the knobs §13 deliberately does *not* add (the carried-turn window,
reply timeout and semaphore are the tutor's, reused), and
``scripts/e2e_backend.py`` stubbing the shaper slot and opening the flag (a
missed assignment would send the browser suite at a live provider, a missed flag
would hide the surface the suite is there to drive). The ``stub`` production
guard is covered slot-by-slot by ``test_config_models.py``'s parametrized tests.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aleph.config import STUB_MODEL_ID, Settings
from aleph.services.feature_flags import FeatureFlag
from scripts.e2e_backend import create_stub_app

# Every env var this block reads, cleared so the "defaults" assertions below
# describe the code defaults rather than whatever the ambient environment says.
_SHAPING_ENV_VARS = (
    "MODEL_SHAPER",
    "MAX_LESSONS_PER_PROPOSAL",
    "RATE_LIMIT_SHAPING_MESSAGES_PER_DAY",
)


@pytest.fixture
def default_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """``Settings`` from code defaults only — no ambient ``.env`` or env vars.

    Same isolation as ``test_config_tutor``: ``_env_file=None`` skips the dotenv
    read, and the shaping vars are deleted from the process environment.
    """
    for name in _SHAPING_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return Settings(_env_file=None)  # ty: ignore[unknown-argument]


def test_shaping_defaults_match_tdd_section_13(default_settings: Settings) -> None:
    # Phase 2B TDD §13, with one deliberate departure. The shaper slot starts on
    # the same strong model as every other slot (the uniform-start discipline,
    # D10); the cap knob ships disabled, the 2A posture. The per-Proposal lesson
    # cap is the departure: §13's provisional 5 could not express one coherent
    # unit-sized edit, so it is 12 (see ``config.py``'s note on what that costs).
    assert default_settings.model_shaper == "anthropic/claude-sonnet-5"
    assert default_settings.max_lessons_per_proposal == 12  # noqa: PLR2004 - the shipped cap
    assert default_settings.rate_limit_shaping_messages_per_day == 0


def test_shaping_reuses_the_tutor_reply_knobs(default_settings: Settings) -> None:
    """§13: the carried-turn window, reply timeout and semaphore are *not* new.

    Shaping deliberately shares ``TUTOR_CONTEXT_TURNS`` (one notion of "recent
    conversation") and ``TUTOR_REPLY_TIMEOUT`` / ``MAX_CONCURRENT_TUTOR_REPLIES``
    (one budget for the two interactive reply kinds, D11). A parallel
    ``shaping_*`` knob appearing here would be the design drifting, so the
    absence is asserted rather than left to review.
    """
    for absent, reused in (
        ("shaping_context_turns", "tutor_context_turns"),
        ("shaping_reply_timeout", "tutor_reply_timeout"),
        ("max_concurrent_shaping_replies", "max_concurrent_tutor_replies"),
    ):
        assert not hasattr(default_settings, absent), (
            f"{absent} must not exist: §13 reuses {reused}"
        )
        assert hasattr(default_settings, reused), f"{reused} is the knob §13 reuses"
    # The reused knobs' *values* are pinned once, in ``test_config_tutor.py`` —
    # re-asserting them here would make a tutor retune break two files.


def test_shaping_block_is_env_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    # The block is operated exactly like the Phase 1/2 settings: env vars, no code
    # change (§5.3's refinement direction for the slot is *up or sideways*). One
    # construction pins §13's env-var **names** for the whole block — a field
    # rename would silently break the documented operator contract.
    monkeypatch.setenv("MODEL_SHAPER", "anthropic/claude-opus-4-8")
    monkeypatch.setenv("MAX_LESSONS_PER_PROPOSAL", "3")
    monkeypatch.setenv("RATE_LIMIT_SHAPING_MESSAGES_PER_DAY", "25")

    settings = Settings(_env_file=None)  # ty: ignore[unknown-argument]

    assert settings.model_shaper == "anthropic/claude-opus-4-8"
    assert settings.max_lessons_per_proposal == 3
    assert settings.rate_limit_shaping_messages_per_day == 25


def test_non_positive_proposal_cap_is_rejected() -> None:
    # A cap of 0 is never a valid tuning — it would reject every Proposal the
    # shaper could make. Rejected at startup, like the tutor's bounds.
    # (``rate_limit_shaping_messages_per_day`` is excluded on purpose: there 0
    # means "disabled", the 2A posture.)
    #
    # ``match`` pins *which* constraint fired: ``Settings`` runs several
    # production/coherence validators, and an unanchored ``ValidationError``
    # would be satisfied by any of them.
    with pytest.raises(ValidationError, match="max_lessons_per_proposal"):
        Settings.model_validate({"env": "development", "max_lessons_per_proposal": 0})


# --- scripts/e2e_backend.py ------------------------------------------------
# The Playwright harness boots this factory. It mutates the module-level
# ``settings`` singleton in place, so ``restored_live_settings`` (tests/unit
# conftest) hands over the singleton and puts it back afterwards. The factory
# boots at all, and keeps the Phase 1/2 slots stubbed, in the twin over in
# ``test_config_tutor.py``; asserted here is only AL-301's delta.


def test_e2e_backend_boots_with_the_shaper_slot_stubbed_and_the_flag_on(
    restored_live_settings: Settings,
) -> None:
    """The browser suite must never reach a live provider for a Proposal.

    And it signs in as a plain learner, so the ``shaping`` flag has to be on
    globally or W17–W21 would fail on an absent surface rather than a broken one.
    """
    create_stub_app()

    assert restored_live_settings.model_shaper == STUB_MODEL_ID
    # ``tutor``, ``streaks``, and ``flashcards`` have all since launched and
    # default on in code, which would make an explicit entry here redundant on
    # its own — but this pin is kept for all five rather than thinned to just
    # ``shaping``: the suite asserts against surfaces that must exist, and a
    # silent code-default flip should surface as a failure here before it turns
    # into a confusing "every spec 404s on an absent surface" somewhere else.
    # ``analyst`` (Phase 6, ticket AL-560) joins the other four: unlike them it
    # has NOT launched (dark-by-default in ``services/feature_flags.py``), but
    # the e2e suite's plain ``DEV_USER`` still needs to see the Beats surfaces
    # for W29/W31, exactly the reasoning that already applies to the other
    # four.
    assert restored_live_settings.feature_flag_default_map == {
        str(FeatureFlag.TUTOR): True,
        str(FeatureFlag.SHAPING): True,
        str(FeatureFlag.STREAKS): True,
        str(FeatureFlag.FLASHCARDS): True,
        str(FeatureFlag.ANALYST): True,
    }
    assert restored_live_settings.rate_limit_shaping_messages_per_day == 0
    # Phase 6 (ticket AL-560, code-review follow-up): the analyst's own two
    # settings mutations, pinned here for the identical reason the shaping cap
    # and the flag map above are — neither was pinned anywhere before this,
    # so a silent code-default drift on either would only otherwise surface
    # as W29/W31 getting slower and eventually flaky weeks into a long-lived
    # local `aleph_e2e` database, never as a failing test today. Lowered from
    # its original `1_000` into the low tens (see this factory's own comment
    # on `max_beats_per_learner` for why raising rather than zeroing is still
    # correct, and why `1_000` was not).
    assert restored_live_settings.rate_limit_brief_research_per_day == 0
    assert restored_live_settings.max_beats_per_learner == 30  # noqa: PLR2004
