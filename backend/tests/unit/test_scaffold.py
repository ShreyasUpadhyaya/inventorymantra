from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core import config
from app.core.logging import redact
from app.main import app


def test_health_ok() -> None:
    body = TestClient(app).get("/health").json()
    assert body["status"] == "ok"
    assert body["llm_mode"] in {"fake", "openai"}


def test_app_today_respects_frozen_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_TODAY", "2026-09-30")
    config.get_settings.cache_clear()
    try:
        assert config.app_today() == date(2026, 9, 30)
    finally:
        config.get_settings.cache_clear()


def test_secrets_never_appear_in_settings_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-abcdefghijklmnop")
    config.get_settings.cache_clear()
    try:
        s = config.get_settings()
        assert "sk-test" not in repr(s) and "sk-test" not in str(s.model_dump())
    finally:
        config.get_settings.cache_clear()


@pytest.mark.parametrize(
    "raw, leaked",
    [
        ("key=sk-abcdefghijklmnop1234", "sk-abcdefghijklmnop1234"),
        ("ls lsv2_pt_abcdefghijk12345", "lsv2_pt_abcdefghijk12345"),
        ("postgresql://user:hunter2pass@localhost:5432/db", "hunter2pass"),
        ('{"password": "s3cr3t!"}', "s3cr3t!"),
        ("Bearer eyJhbGciOi.eyJzdWIiOiIx.c2lnbmF0dXJl", "eyJhbGciOi.eyJzdWIiOiIx.c2lnbmF0dXJl"),
    ],
)
def test_redaction(raw: str, leaked: str) -> None:
    assert leaked not in redact(raw)
