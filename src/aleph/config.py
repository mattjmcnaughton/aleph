"""Application configuration via environment variables."""

import datetime

from pydantic import model_validator
from pydantic_settings import BaseSettings


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


settings = Settings()
