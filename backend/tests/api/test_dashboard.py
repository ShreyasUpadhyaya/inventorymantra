"""Role dashboards (C3.3): right content per role, numbers match the frozen answer keys,
and nothing leaks across roles. Business date frozen at 2026-09-30."""

import json
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.auth.tokens import create_access_token
from app.core import config
from app.main import app
from scripts.fabricate import demo_id

DEMO = {
    "inventory_manager": "inventory@demo.inventorymantra",
    "procurement_manager": "procurement@demo.inventorymantra",
    "owner": "owner@demo.inventorymantra",
}


@pytest.fixture(autouse=True)
def _frozen_and_configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("APP_TODAY", "2026-09-30")
    config.get_settings.cache_clear()
    settings = config.get_settings()
    try:
        settings.jwt_secret()
    except RuntimeError:
        pytest.skip("JWT_SECRET_KEY not set")
    if settings.readonly_db_password is None:
        pytest.skip("READONLY_DB_PASSWORD not set")
    yield
    config.get_settings.cache_clear()


def dashboard_for(db: psycopg.Connection, role: str) -> dict[str, Any]:
    """Mint a session for the seeded demo user (bearer token; no password needed)."""
    user_id = demo_id("user", DEMO[role])
    row = db.execute("SELECT token_version FROM users WHERE id = %s", (user_id,)).fetchone()
    assert row is not None, "demo users missing: run make seed"
    token = create_access_token(user_id, row[0])
    r = TestClient(app).get("/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def kpis(d: dict[str, Any]) -> dict[str, Any]:
    return {k["id"]: k["value"] for k in d["kpis"]}


def sections(d: dict[str, Any]) -> dict[str, Any]:
    return {s["id"]: s for s in d["sections"]}


# ------------------------------------------------------------------ content per role
def test_inventory_manager_dashboard(db: psycopg.Connection) -> None:
    d = dashboard_for(db, "inventory_manager")
    assert d["role"] == "inventory_manager" and d["today"] == "2026-09-30"
    assert kpis(d) == {"in_stock": 362, "out_of_stock": 98, "reorder_now": 15,
                       "my_open_requests": 4}  # fmt: skip
    assert list(sections(d)) == [
        "reorder_alerts", "stock_by_location", "inbound", "recent_movements", "my_requests",
    ]  # fmt: skip
    by_location = dict(sections(d)["stock_by_location"]["rows"])
    assert by_location == {"132-1": 266, "132-3": 74, "132-2": 51, "103-1": 14, "C-84": 4}


def test_procurement_manager_dashboard(db: psycopg.Connection) -> None:
    d = dashboard_for(db, "procurement_manager")
    assert kpis(d)["open_po_value"] == 4789288.83
    assert kpis(d)["overdue_pos"] == 28
    s = sections(d)
    assert s["overdue_pos"]["rows"][0][:2] == ["PO-2026-0009", "Granite Steels Pvt. Ltd."]
    assert "preferred_vendor" in s["reorder_alerts"]["columns"]
    assert s["po_status"]["viz"] == "pie" and s["spend_by_month"]["viz"] == "line"


def test_owner_dashboard(db: psycopg.Connection) -> None:
    d = dashboard_for(db, "owner")
    assert kpis(d)["revenue_ytd"] == 38564700
    assert kpis(d)["last_quarter_margin"] == 20.9  # Q2 2026: last *completed* quarter
    margins = {r[0]: r[-1] for r in sections(d)["margins_by_quarter"]["rows"]}
    assert margins == {"2025-Q4": 23.6, "2026-Q1": 25.2, "2026-Q2": 20.9, "2026-Q3": 24.7}
    assert sections(d)["pending_approvals"]["rows"] == []
    assert sections(d)["pending_approvals"]["empty_message"]


# ------------------------------------------------------------------ no leaks across roles
IM_FORBIDDEN = ["vendor", "price", "cost", "amount", "total", "spend", "margin", "revenue",
                "Sterling", "Granite", "Shakti", "4789288", "@demo.inventorymantra"]  # fmt: skip
OWNER_ONLY = ["margins_by_quarter", "pending_approvals", "recent_activity", "revenue_ytd",
              "last_quarter_margin"]  # fmt: skip


def test_inventory_manager_dashboard_leaks_nothing(db: psycopg.Connection) -> None:
    text = json.dumps(dashboard_for(db, "inventory_manager"), ensure_ascii=False)
    assert [w for w in IM_FORBIDDEN if w.lower() in text.lower()] == []


def test_procurement_manager_sees_no_owner_data(db: psycopg.Connection) -> None:
    d = dashboard_for(db, "procurement_manager")
    ids = set(kpis(d)) | set(sections(d))
    assert ids.isdisjoint(OWNER_ONLY)
    text = json.dumps(d).lower()
    assert "margin" not in text and "revenue" not in text and "@demo.inventorymantra" not in text


# ------------------------------------------------------------------ shape and access
@pytest.mark.parametrize("role", list(DEMO))
def test_sections_are_renderable(db: psycopg.Connection, role: str) -> None:
    for s in dashboard_for(db, role)["sections"]:
        assert all(len(row) == len(s["columns"]) for row in s["rows"])
        if s["viz"] != "table":
            assert s["x"] in s["columns"] and set(s["y"]) <= set(s["columns"])


def test_dashboard_requires_login() -> None:
    assert TestClient(app).get("/dashboard").status_code == 401


def test_missing_readonly_users_give_503(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = demo_id("user", DEMO["owner"])
    row = db.execute("SELECT token_version FROM users WHERE id = %s", (user_id,)).fetchone()
    assert row is not None
    token = create_access_token(user_id, row[0])
    monkeypatch.setenv("READONLY_DB_PASSWORD", "definitely-wrong")
    config.get_settings.cache_clear()
    r = TestClient(app).get("/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 503
    assert "definitely-wrong" not in r.text
