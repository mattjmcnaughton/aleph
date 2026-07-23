"""Application configuration via environment variables."""

import datetime
from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings

# The config-selectable id (TDD §12/D9) that resolves to the deterministic stub
# model instead of an OpenRouter-backed one. Guarded out of production below.
STUB_MODEL_ID = "stub"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    log_level: str = "INFO"
    log_format: str = "json"
    host: str = "0.0.0.0"
    port: int = 8000

    otel_exporter_otlp_endpoint: str = "http://localhost:4318"

    database_url: str = "postgresql+asyncpg://localhost:5432/aleph"

    # Generation timings (TDD §5.4 / §14). A model call is bounded by
    # ``generation_timeout_seconds`` so ``failed`` is always reached (no dead
    # spinners); a row stuck in ``generating`` past
    # ``generation_stale_after_seconds`` is treated as failed and re-claimable,
    # so a crashed/restarted process self-heals. Stale MUST exceed the timeout
    # (+ overhead), else a healthy slow generation gets double-claimed — a
    # tested invariant (``_check_generation_timings``), not a comment.
    generation_timeout_seconds: int = 60
    generation_stale_after_seconds: int = 180

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @model_validator(mode="after")
    def _check_generation_timings(self) -> "Settings":
        if self.generation_stale_after_seconds <= self.generation_timeout_seconds:
            msg = (
                "generation_stale_after_seconds "
                f"({self.generation_stale_after_seconds}) must exceed "
                f"generation_timeout_seconds ({self.generation_timeout_seconds}): "
                "otherwise a healthy slow generation is double-claimed (TDD §5.4)."
            )
            raise ValueError(msg)
        return self

    @property
    def generation_timeout(self) -> datetime.timedelta:
        """Per model-call timeout as a timedelta."""
        return datetime.timedelta(seconds=self.generation_timeout_seconds)

    @property
    def generation_stale_after(self) -> datetime.timedelta:
        """Stale-recovery window as a timedelta."""
        return datetime.timedelta(seconds=self.generation_stale_after_seconds)

    # --- AL-030: OpenRouter model routing (TDD §5.3, §14) ---------------------
    # Appended as a self-contained block; the model slots resolve through
    # ``services/openrouter.py``. ``ENV=production`` forbids the ``stub`` id so
    # the deterministic CI/e2e model can never be reached in prod.

    # Deployment environment. A closed ``Literal`` (not a free ``str``) so a
    # typo like ``ENV=prod`` is rejected at startup rather than silently
    # counting as non-production and disabling the stub guard below.
    env: Literal["development", "test", "production"] = "development"

    # OpenRouter credential (empty locally / in CI; the stub needs no key).
    openrouter_api_key: str = ""

    # The three model slots (TDD §8/§5.3). All start on one strong model — no
    # premature tiering; per-slot refinement is driven by evals + cost data.
    model_outline: str = "anthropic/claude-sonnet-5"
    model_lesson: str = "anthropic/claude-sonnet-5"
    model_judge: str = "anthropic/claude-sonnet-5"

    # Comma-separated OpenRouter ids an admin may select per-request for the
    # outline/lesson slots (the picker allowlist, D14/§5.3), in display order.
    model_allowlist: str = (
        "anthropic/claude-sonnet-5,"
        "anthropic/claude-haiku-4-5,"
        "anthropic/claude-opus-4-8,"
        "openai/gpt-5.6-terra,"
        "minimax/minimax-m3"
    )

    @property
    def allowlist_ids(self) -> tuple[str, ...]:
        """Parsed ``model_allowlist``: stripped, empties dropped, order kept."""
        return tuple(
            candidate.strip()
            for candidate in self.model_allowlist.split(",")
            if candidate.strip()
        )

    @property
    def is_production(self) -> bool:
        """Whether this is a production deployment.

        Exact match: ``env`` is a closed ``Literal`` so no normalization is
        needed — any non-``production`` value could only be ``development`` or
        ``test``, and an out-of-set value never validates.
        """
        return self.env == "production"

    @model_validator(mode="after")
    def _forbid_stub_in_production(self) -> Self:
        """Fail fast at startup if the stub could be reached in production.

        The stub is the deterministic CI/e2e model (D9); reaching it in
        production would silently serve canned content, so it is rejected here
        rather than at resolution time. This covers both the three fixed model
        slots *and* the admin picker's ``MODEL_ALLOWLIST`` — once AL-052's
        per-request picker lands, an allowlisted ``stub`` would let an admin
        select it in prod and call ``resolve_model("stub")``, bypassing a
        slot-only guard.
        """
        if self.is_production:
            offenders = [
                slot
                for slot in ("model_outline", "model_lesson", "model_judge")
                if getattr(self, slot) == STUB_MODEL_ID
            ]
            if STUB_MODEL_ID in self.allowlist_ids:
                offenders.append("model_allowlist")
            if offenders:
                joined = ", ".join(offenders)
                raise ValueError(
                    f"The 'stub' model is not allowed in production (ENV=production); "
                    f"offending slot(s): {joined}."
                )
        return self


settings = Settings()
