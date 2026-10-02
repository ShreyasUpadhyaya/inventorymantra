"""A missing or weak JWT_SECRET_KEY must give a clear 503, never a bare 500."""

from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core import config
from app.main import app


@pytest.fixture(params=["", "too-short"], ids=["missing", "too-short"])
def weak_secret(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET_KEY", request.param)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


@pytest.mark.usefixtures("weak_secret")
def test_login_and_refresh_explain_the_misconfiguration(db: psycopg.Connection) -> None:
    client = TestClient(app)
    login = client.post("/auth/login", json={"email": "a@b.c", "password": "x"})
    assert login.status_code == 503
    assert "JWT_SECRET_KEY" in login.json()["detail"]
    assert client.post("/auth/refresh").status_code == 503


@pytest.mark.usefixtures("weak_secret")
def test_me_reports_503_instead_of_crashing(db: psycopg.Connection) -> None:
    client = TestClient(app)
    r = client.get("/auth/me", headers={"Authorization": "Bearer anything"})
    assert r.status_code == 503
