"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    log_level: str = "INFO"
    log_format: str = "json"
    host: str = "0.0.0.0"
    port: int = 8000

    otel_exporter_otlp_endpoint: str = "http://localhost:4318"

    database_url: str = "postgresql+asyncpg://localhost:5432/aleph"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
