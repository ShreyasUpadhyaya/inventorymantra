"""LangSmith tracing setup.

pydantic-settings reads .env without exporting it, and LangSmith only reads os.environ.
The API and CLI call `configure_tracing()` at start-up to export the LangSmith settings;
tests never call it, so test runs never send traces.
"""

import hashlib
import os

from app.core.config import get_settings


def configure_tracing() -> bool:
    settings = get_settings()
    if not (settings.langsmith_tracing and settings.langsmith_api_key):
        os.environ["LANGSMITH_TRACING"] = "false"
        return False
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key.get_secret_value()
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    return True


def tracing_enabled() -> bool:
    return os.environ.get("LANGSMITH_TRACING", "").lower() == "true"


def user_ref(user_id: object) -> str:
    """A stable pseudonymous reference for trace metadata (no raw ids or emails)."""
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:12]
