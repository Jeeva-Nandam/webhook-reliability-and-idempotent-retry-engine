"""
Centralized application configuration.

Everything that varies between environments (dev / test / CI / prod) lives
here and is loaded from environment variables, never hardcoded. This makes
the app 12-factor: the same Docker image runs anywhere by swapping env vars.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- App ---
    APP_NAME: str = "Webhook Reliability & Idempotent Retry Engine"
    ENVIRONMENT: str = "development"
    API_V1_PREFIX: str = "/api/v1"

    # --- Database ---
    DATABASE_URL: str = "postgresql+psycopg://webhook:webhook@postgres:5432/webhook_db"

    # --- Redis / Celery ---
    REDIS_URL: str = "redis://redis:6379/0"
    CELERY_BROKER_URL: str = "redis://redis:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/1"

    # --- Security ---
    WEBHOOK_SECRET: str = "4a6e8b2f9c1d0e3f5a7b9c0d1e2f3a4b5c6d7e8f9a1b1c2d3e4j5a6b7c8d9e0f"
    SIGNATURE_HEADER: str = "X-Webhook-Signature"

    # --- Retry policy defaults ---
    DEFAULT_MAX_RETRIES: int = 5
    RETRY_BASE_DELAY_SECONDS: int = 1
    RETRY_MAX_DELAY_SECONDS: int = 300
    RETRY_JITTER_SECONDS: float = 2.0

    # --- Pagination ---
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """
    Cached settings instance. lru_cache means the .env file / environment is
    only parsed once per process, not on every request.
    """
    return Settings()
