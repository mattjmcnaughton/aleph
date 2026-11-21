"""Unit tests for the AL-501 Phase 6 analyst config block (TDD §13, D6/D7/D14/D14a).

New file (AL-501) so it never collides with other tickets editing ``test_config``,
the ``test_config_shaping.py`` precedent. This
ticket is config-only — no retrieval code, no models, no service, no router — so
the tests here stay narrow: §13's defaults, the §13 env-var names, the stale >
timeout invariant (mirroring ``_check_generation_timings``), and that startup
succeeds with ``EXA_API_KEY`` unset.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aleph.config import Settings

# Every env var this block reads, cleared so the "defaults" assertions below
# describe the code defaults rather than whatever the ambient environment says.
_ANALYST_ENV_VARS = (
    "EXA_API_KEY",
    "MAX_BEATS_PER_LEARNER",
    "RATE_LIMIT_BRIEF_RESEARCH_PER_DAY",
    "MAX_CONCURRENT_BRIEF_RESEARCH",
    "BRIEF_RETRIEVAL_MAX_QUERIES",
    "BRIEF_RETRIEVAL_MAX_DOCUMENTS",
    "BRIEF_RETRIEVAL_TEXT_BUDGET_CHARS",
    "BRIEF_RESEARCH_TIMEOUT_SECONDS",
    "BRIEF_RESEARCH_STALE_AFTER_SECONDS",
)


@pytest.fixture
def default_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """``Settings`` from code defaults only — no ambient ``.env`` or env vars.

    Same isolation as ``test_config_tutor``:
    ``_env_file=None`` skips the dotenv read, and the analyst vars are deleted
    from the process environment.
    """
    for name in _ANALYST_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return Settings(_env_file=None)  # ty: ignore[unknown-argument]


def test_analyst_defaults_match_tdd_section_13(default_settings: Settings) -> None:
    assert default_settings.exa_api_key == ""
    assert default_settings.max_beats_per_learner == 3  # noqa: PLR2004 - TDD §13
    assert default_settings.rate_limit_brief_research_per_day == 5  # noqa: PLR2004
    assert default_settings.max_concurrent_brief_research == 2  # noqa: PLR2004
    assert default_settings.brief_retrieval_max_queries == 6  # noqa: PLR2004
    assert default_settings.brief_retrieval_max_documents == 12  # noqa: PLR2004
    assert default_settings.brief_retrieval_text_budget_chars == 160_000  # noqa: PLR2004
    assert default_settings.brief_research_timeout_seconds == 180  # noqa: PLR2004
    assert default_settings.brief_research_stale_after_seconds == 420  # noqa: PLR2004


def test_analyst_block_is_env_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    # One construction pins §13's env-var **names** for the whole block — a
    # field rename would silently break the documented operator contract.
    monkeypatch.setenv("EXA_API_KEY", "exa-test-key")
    monkeypatch.setenv("MAX_BEATS_PER_LEARNER", "5")
    monkeypatch.setenv("RATE_LIMIT_BRIEF_RESEARCH_PER_DAY", "10")
    monkeypatch.setenv("MAX_CONCURRENT_BRIEF_RESEARCH", "4")
    monkeypatch.setenv("BRIEF_RETRIEVAL_MAX_QUERIES", "3")
    monkeypatch.setenv("BRIEF_RETRIEVAL_MAX_DOCUMENTS", "20")
    monkeypatch.setenv("BRIEF_RETRIEVAL_TEXT_BUDGET_CHARS", "50000")
    monkeypatch.setenv("BRIEF_RESEARCH_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("BRIEF_RESEARCH_STALE_AFTER_SECONDS", "300")

    settings = Settings(_env_file=None)  # ty: ignore[unknown-argument]

    assert settings.exa_api_key == "exa-test-key"
    assert settings.max_beats_per_learner == 5  # noqa: PLR2004
    assert settings.rate_limit_brief_research_per_day == 10  # noqa: PLR2004
    assert settings.max_concurrent_brief_research == 4  # noqa: PLR2004
    assert settings.brief_retrieval_max_queries == 3  # noqa: PLR2004
    assert settings.brief_retrieval_max_documents == 20  # noqa: PLR2004
    assert settings.brief_retrieval_text_budget_chars == 50000  # noqa: PLR2004
    assert settings.brief_research_timeout_seconds == 120  # noqa: PLR2004
    assert settings.brief_research_stale_after_seconds == 300  # noqa: PLR2004


# --- startup succeeds with EXA_API_KEY unset -----------------------------------


def test_app_boots_with_exa_api_key_unset(
    restored_live_settings: Settings,
) -> None:
    """§12: the Exa credential is optional at startup — nothing reads it yet.

    ``create_app`` reads the module-level ``settings`` singleton (it takes no
    arguments), so this pins the actual boot path rather than a fresh
    ``Settings()`` construction — ``restored_live_settings`` snapshots and
    restores it, the same seam ``scripts/e2e_backend.py`` tests use.
    """
    restored_live_settings.exa_api_key = ""

    from aleph.app import create_app

    app = create_app()
    assert app.title == "aleph"


# --- stale > timeout (mirrors test_config_generation.py) -----------------------


def test_stale_after_equal_to_timeout_is_rejected() -> None:
    with pytest.raises(ValidationError, match="brief_research_stale_after_seconds"):
        Settings(
            _env_file=None,  # ty: ignore[unknown-argument]
            brief_research_timeout_seconds=180,
            brief_research_stale_after_seconds=180,
        )


def test_stale_after_below_timeout_is_rejected() -> None:
    with pytest.raises(ValidationError, match="brief_research_stale_after_seconds"):
        Settings(
            _env_file=None,  # ty: ignore[unknown-argument]
            brief_research_timeout_seconds=180,
            brief_research_stale_after_seconds=90,
        )


def test_stale_after_above_timeout_is_accepted() -> None:
    settings = Settings(
        _env_file=None,  # ty: ignore[unknown-argument]
        brief_research_timeout_seconds=180,
        brief_research_stale_after_seconds=420,
    )
    assert settings.brief_research_stale_after_seconds == 420  # noqa: PLR2004
