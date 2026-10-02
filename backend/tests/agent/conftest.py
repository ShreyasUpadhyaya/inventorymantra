import uuid
from collections.abc import Iterator

import pytest

from app.auth.service import AuthUser
from app.core import config


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Agent tests never call OpenAI or send LangSmith traces."""
    monkeypatch.setenv("LLM_MODE", "fake")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("APP_TODAY", "2026-09-30")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def make_user(role: str) -> AuthUser:
    return AuthUser(
        id=uuid.uuid4(), email=f"{role}@test", full_name="Test", role=role,  # type: ignore[arg-type]
        is_active=True, token_version=0,
    )  # fmt: skip
