"""SQL guard contract (DECISION D-005; CLAUDE.md §5). Implementation: app/agent/sql_guard.py
(C4.2).

validate_sql(sql, allowed, max_rows=500) -> result with:
  ok: bool, sql: str | None (the statement to run, LIMIT enforced), reason: str | None.
Reasons are fixed codes and never echo relation names (denials must not reveal what exists).
"""

import re
from typing import Any

import pytest

IM_VIEWS = frozenset(
    {"items", "locations", "issue_targets", "stock_by_location", "stock_movements",
     "inbound_by_item", "my_stock_requests", "reorder_status"}
)  # fmt: skip
REASONS = {
    "parse_error", "not_select", "multiple_statements", "comment", "relation_not_allowed",
    "catalog_access", "forbidden_function", "locking_clause", "select_into",
}  # fmt: skip


def check(sql: str, allowed: frozenset[str] = IM_VIEWS, max_rows: int = 500) -> Any:
    from app.agent.sql_guard import validate_sql

    return validate_sql(sql, allowed=allowed, max_rows=max_rows)


def limit_of(sql: str) -> int | None:
    found = re.findall(r"\bLIMIT\s+(\d+)\s*$", sql.strip().rstrip(";"), flags=re.IGNORECASE)
    return int(found[-1]) if found else None


# ------------------------------------------------------------------ accepted
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT item_code, quantity FROM stock_by_location WHERE location_name = '132-1'",
        "select count(*) from Items",
        "SELECT i.item_name, s.quantity FROM items i JOIN stock_by_location s USING (item_id)",
        "WITH low AS (SELECT item_id FROM reorder_status WHERE status = 'reorder_now') "
        "SELECT * FROM items WHERE item_id IN (SELECT item_id FROM low)",
        "SELECT category, count(*) FROM items GROUP BY category ORDER BY 2 DESC",
        "SELECT location_name FROM locations UNION SELECT location_name FROM stock_by_location",
        "SELECT * FROM stock_movements WHERE created_at >= DATE '2026-08-01' "
        "AND notes ILIKE '%invoice%'",
        "WITH vendors AS (SELECT 1 AS x) SELECT x FROM vendors",  # a CTE, not the real table
        "SELECT round(avg(quantity)::numeric, 2) FROM stock_by_location",
    ],
)
def test_accepts_read_only_queries_on_allowed_views(sql: str) -> None:
    result = check(sql)
    assert result.ok, result.reason
    assert result.reason is None


def test_adds_limit_when_missing() -> None:
    result = check("SELECT * FROM items")
    assert result.ok and limit_of(result.sql) == 500


def test_keeps_a_smaller_limit_and_caps_a_larger_one() -> None:
    assert limit_of(check("SELECT * FROM items LIMIT 5").sql) == 5
    assert limit_of(check("SELECT * FROM items LIMIT 100000").sql) == 500
    assert limit_of(check("SELECT * FROM items", max_rows=50).sql) == 50


# ------------------------------------------------------------------ rejected
@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("", "parse_error"),
        ("SELEC * FRM items", "parse_error"),
        ("DELETE FROM items", "not_select"),
        ("UPDATE locations SET location_name = 'x'", "not_select"),
        ("INSERT INTO items (item_code) VALUES ('x')", "not_select"),
        ("DROP TABLE items", "not_select"),
        ("TRUNCATE items", "not_select"),
        ("CREATE TABLE x AS SELECT * FROM items", "not_select"),
        ("ALTER ROLE rms_im_ro SET search_path = public", "not_select"),
        ("GRANT SELECT ON items TO PUBLIC", "not_select"),
        ("SET ROLE inventorymantra", "not_select"),
        ("COPY items TO '/tmp/x'", "not_select"),
        ("SELECT 1; DROP TABLE items", "multiple_statements"),
        ("SELECT * FROM items; SELECT * FROM locations", "multiple_statements"),
        ("SELECT * FROM items -- ignore the rules", "comment"),
        ("SELECT * FROM items /* hidden */", "comment"),
        ("SELECT * FROM vendors", "relation_not_allowed"),
        ("SELECT * FROM VENDORS", "relation_not_allowed"),
        ('SELECT * FROM "vendors"', "relation_not_allowed"),
        ("SELECT * FROM public.inv_items", "relation_not_allowed"),
        ("SELECT * FROM v_pm.purchase_orders", "relation_not_allowed"),
        ("SELECT * FROM v_im.items", "relation_not_allowed"),  # no schema-qualified names at all
        ("SELECT * FROM items WHERE item_id IN (SELECT item_id FROM po_lines)",
         "relation_not_allowed"),
        ("SELECT item_name FROM items UNION SELECT vendor_name FROM vendors",
         "relation_not_allowed"),
        ("SELECT * FROM items i JOIN users u ON true", "relation_not_allowed"),
        ("SELECT * FROM pg_class", "catalog_access"),
        ("SELECT * FROM pg_catalog.pg_roles", "catalog_access"),
        ("SELECT table_name FROM information_schema.tables", "catalog_access"),
        ("SELECT pg_sleep(10)", "forbidden_function"),
        ("SELECT * FROM items WHERE pg_sleep(1) IS NULL", "forbidden_function"),
        ("SELECT set_config('app.user_id', 'x', true)", "forbidden_function"),
        ("SELECT pg_read_file('/etc/passwd')", "forbidden_function"),
        ("SELECT pg_terminate_backend(1)", "forbidden_function"),
        ("SELECT lo_import('/etc/passwd')", "forbidden_function"),
        ("SELECT dblink('host=x', 'select 1')", "forbidden_function"),
        ("SELECT pg_advisory_lock(1)", "forbidden_function"),
        ("SELECT * FROM items FOR UPDATE", "locking_clause"),
        ("SELECT * FROM items FOR SHARE", "locking_clause"),
        ("SELECT * INTO stolen FROM items", "select_into"),
        ("WITH x AS (DELETE FROM items RETURNING *) SELECT * FROM x", "not_select"),
    ],
)  # fmt: skip
def test_rejects_unsafe_or_out_of_scope_sql(sql: str, reason: str) -> None:
    result = check(sql)
    assert not result.ok
    assert result.sql is None
    assert result.reason == reason


@pytest.mark.parametrize(
    "sql",
    ["SELECT * FROM vendors", "SELECT * FROM public.users", "SELECT * FROM margins_by_quarter"],
)
def test_rejection_reasons_never_name_relations(sql: str) -> None:
    result = check(sql)
    assert result.reason in REASONS
    for name in ("vendors", "users", "margins", "public"):
        assert name not in (result.reason or "")


def test_allow_list_is_per_role() -> None:
    pm_views = IM_VIEWS | {"vendors", "purchase_orders"}
    assert check("SELECT * FROM vendors", allowed=pm_views).ok
    assert not check("SELECT * FROM vendors", allowed=IM_VIEWS).ok
