"""Demo data is deterministic, labelled, within the approved ranges, and logins work.

Each test runs fabricate() inside a transaction that is rolled back, so the seeded
database is left exactly as `make seed` produced it.
"""

from collections.abc import Iterator
from decimal import Decimal

import psycopg
import pytest

from app.auth.passwords import verify_password
from app.core import config
from app.db.session import psycopg_dsn
from scripts.fabricate import DEMO_USERS, fabricate

FINGERPRINT_SQL = """
SELECT
  (SELECT string_agg(item_id || ':' || lead_time_days || ':' || min_level || ':' || reorder_qty,
                     ',' ORDER BY item_id) FROM reorder_policy),
  (SELECT sum(line_revenue) FROM sales_order_lines),
  (SELECT string_agg(so_number || customer_name || status, ',' ORDER BY so_number)
     FROM sales_orders),
  (SELECT string_agg(item_id || status, ',' ORDER BY item_id) FROM stock_requests)
"""


@pytest.fixture
def tx(db: psycopg.Connection) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(psycopg_dsn(), connect_timeout=3)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def demo_password(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("DEMO_PASSWORD", "test-demo-pass")
    config.get_settings.cache_clear()
    yield "test-demo-pass"
    config.get_settings.cache_clear()


def test_is_deterministic(tx: psycopg.Connection, demo_password: str) -> None:
    fabricate(tx)
    first = tx.execute(FINGERPRINT_SQL).fetchone()
    fabricate(tx)
    assert tx.execute(FINGERPRINT_SQL).fetchone() == first


def test_demo_users_can_log_in(tx: psycopg.Connection, demo_password: str) -> None:
    summary = fabricate(tx)
    assert summary.generated_password is None  # DEMO_PASSWORD was used
    rows = tx.execute("SELECT email, role, password_hash, is_active FROM users").fetchall()
    assert {(r[0], r[1]) for r in rows} == {(email, role) for email, _, role in DEMO_USERS}
    for _, _, password_hash, active in rows:
        assert active and verify_password(demo_password, password_hash)
        assert not verify_password("wrong-password", password_hash)
        assert demo_password not in password_hash


def test_every_fabricated_row_is_labelled_demo(tx: psycopg.Connection) -> None:
    fabricate(tx)
    for table in ("reorder_policy", "stock_requests", "sales_orders"):
        row = tx.execute(f"SELECT count(*) FILTER (WHERE source <> 'demo') FROM {table}").fetchone()  # noqa: S608
        assert row == (0,)


def test_sales_margins_within_approved_range(tx: psycopg.Connection) -> None:
    fabricate(tx)
    row = tx.execute(
        """SELECT min((unit_price - unit_cost) / unit_price),
                  max((unit_price - unit_cost) / unit_price), min(order_date), max(order_date)
           FROM sales_order_lines l JOIN sales_orders o ON o.id = l.sales_order_id"""
    ).fetchone()
    assert row is not None
    min_margin, max_margin, first, last = row
    assert Decimal("0.14") <= min_margin and max_margin <= Decimal("0.32")
    assert (str(first)[:7], str(last)[:7]) == ("2025-10", "2026-09")


def test_only_finished_goods_are_sold(tx: psycopg.Connection) -> None:
    fabricate(tx)
    row = tx.execute(
        """SELECT count(*) FROM sales_order_lines l JOIN inv_items i ON i.id = l.item_id
           LEFT JOIN inv_categories c ON c.id = i.category_id
           WHERE c.short_code IS DISTINCT FROM 'FG'"""
    ).fetchone()
    assert row == (0,)


def test_reorder_policies_produce_demo_alerts(tx: psycopg.Connection) -> None:
    fabricate(tx)
    row = tx.execute(
        """
        WITH onh AS (SELECT item_id, sum(quantity) q FROM inv_current_stock GROUP BY 1),
        use AS (SELECT item_id, sum(-quantity) / 90.0 d FROM inv_transactions
                WHERE transaction_type = 'outbound' AND created_at >= '2026-07-02' GROUP BY 1)
        SELECT count(*), min(lead_time_days), max(lead_time_days),
               count(*) FILTER (WHERE coalesce(o.q, 0) <= u.d * r.lead_time_days + r.safety_stock)
        FROM reorder_policy r JOIN use u USING (item_id) LEFT JOIN onh o USING (item_id)
        """
    ).fetchone()
    assert row is not None
    policies, min_lead, max_lead, alerts = row
    assert policies > 20 and min_lead >= 7 and max_lead <= 45
    assert 5 <= alerts <= 30  # enough to demo, not a wall of noise


def test_stock_requests_come_from_the_inventory_manager(tx: psycopg.Connection) -> None:
    fabricate(tx)
    rows = tx.execute(
        """SELECT s.status, u.role FROM stock_requests s JOIN users u ON u.id = s.requested_by"""
    ).fetchall()
    assert {role for _, role in rows} == {"inventory_manager"}
    assert sorted(status for status, _ in rows).count("open") == 4


def test_po_status_and_sequence_guards(tx: psycopg.Connection) -> None:
    last_value, is_called = tx.execute(
        "SELECT last_value, is_called FROM po_number_seq"
    ).fetchone() or (0, True)
    assert (last_value, is_called) == (58, False)  # next PO is PO-2026-0058
    with pytest.raises(psycopg.errors.CheckViolation):
        tx.execute(
            "UPDATE proc_purchase_orders SET status = 'shipped' WHERE po_number = 'PO-2026-0003'"
        )
