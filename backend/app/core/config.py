"""Application settings. Secrets are loaded from the environment / .env only (CLAUDE.md §7)."""

from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
TIMEZONE = ZoneInfo("Asia/Kolkata")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: SecretStr | None = None
    jwt_secret_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    langsmith_api_key: SecretStr | None = None
    demo_password: SecretStr | None = None

    llm_mode: Literal["fake", "openai"] = "fake"
    openai_model_reasoning: str = ""
    openai_model_fast: str = ""
    openai_daily_token_budget: int = 200_000

    app_today: date | None = None
    po_approval_threshold_inr: int = 100_000
    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def app_today() -> date:
    """'Today' for all business logic: APP_TODAY if set (evals), else the date in IST."""
    frozen = get_settings().app_today
    return frozen if frozen is not None else datetime.now(TIMEZONE).date()
