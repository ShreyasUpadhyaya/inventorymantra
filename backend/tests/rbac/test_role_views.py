"""The database itself enforces role visibility (DECISION D-004, CLAUDE.md §5).

These tests connect as the real read-only role users, the same way agent SQL will.
They need `make seed` with READONLY_DB_PASSWORD set; CI always sets it.
"""

import re
import uuid
from collections.abc import Iterator
from datetime import date

import psycopg
import pytest

from app.core.config import get_settings
from app.db.readonly import VIEW_SCHEMA, AppRole, readonly_session, role_dsn
from scripts.fabricate import demo_id

ROLES: list[AppRole] = ["inventory_manager", "procurement_manager", "owner"]
TODAY = date(2026, 9, 30)
USER_ID = {
    "inventory_manager": demo_id("user", "inventory@demo.inventorymantra"),
    "procurement_manager": demo_id("user", "procurement@demo.inventorymantra"),
    "owner": demo_id("user", "owner@demo.inventorymantra"),
}

# Column names an Inventory Manager must never see (prices, vendors, money, people, secrets).
IM_FORBIDDEN = re.compile(
    r"price|cost|amount|total|spend|vendor|payment|tranche|margin|revenue|profit|email|"
    r"password|hash|token|customer"
)
# Column names a Procurement Manager must never see (sales, people, secrets).
PM_FORBIDDEN = re.compile(r"margin|revenue|profit|customer|email|password|hash|token")
OWNER_ONLY_VIEWS = {"sales_orders", "sales_lines", "margins_by_quarter", "approvals", "users",
                    "audit_log"}  # fmt: skip


@pytest.fixture(scope="module", autouse=True)
def _requires_role_logins(db: psycopg.Connection) -> None:
    if get_settings().readonly_db_password is None:
        pytest.skip("READONLY_DB_PASSWORD not set; run `make seed` with it to test RBAC")


def all_views(db: psycopg.Connection) -> dict[str, set[str]]:
    rows = db.execute(
        "SELECT table_schema, table_name FROM information_schema.views"
        " WHERE table_schema IN ('v_im', 'v_pm', 'v_owner')"
    ).fetchall()
    views: dict[str, set[str]] = {}
    for schema, name in rows:
        views.setdefault(schema, set()).add(name)
    return views


def columns(db: psycopg.Connection, schema: str) -> list[tuple[str, str]]:
    return db.execute(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = %s",
        (schema,),
    ).fetchall()


@pytest.fixture
def session(request: pytest.FixtureRequest) -> Iterator[psycopg.Connection]:
    role: AppRole = request.param
    with readonly_session(role, USER_ID[role], TODAY) as conn:
        yield conn


# ------------------------------------------------------------------ access matrix
def test_access_matrix_every_role_every_view(db: psycopg.Connection) -> None:
    """A role can read every view in its own schema and nothing in the others."""
    views = all_views(db)
    assert {k: len(v) for k, v in views.items()} == {"v_im": 8, "v_pm": 19, "v_owner": 22}
    for role in ROLES:
        own = VIEW_SCHEMA[role]
        with psycopg.connect(role_dsn(role), connect_timeout=5, autocommit=True) as conn:
            for schema, names in views.items():
                for name in sorted(names):
                    target = f"{schema}.{name}"
                    if schema == own:
                        conn.execute(f"SELECT * FROM {target} LIMIT 1")  # noqa: S608
                    else:
                        with pytest.raises(psycopg.errors.InsufficientPrivilege):
                            conn.execute(f"SELECT * FROM {target} LIMIT 1")  # noqa: S608


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(
    "table", ["inv_items", "inv_current_stock", "proc_vendors", "proc_po_lines", "users",
              "sales_orders", "agent_audit_log", "refresh_tokens"],
)  # fmt: skip
def test_base_tables_are_never_readable(role: AppRole, table: str) -> None:
    with (
        psycopg.connect(role_dsn(role), connect_timeout=5, autocommit=True) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute(f"SELECT 1 FROM public.{table} LIMIT 1")  # noqa: S608


# ------------------------------------------------------------------ no writes, no escalation
@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TEMP TABLE t (x int)",
        "CREATE TABLE public.t (x int)",
        "DELETE FROM items",
        "UPDATE locations SET location_name = 'x'",
        "INSERT INTO public.inv_locations (id, name) VALUES ('x', 'x')",
        "SET ROLE inventorymantra",
        "SET SESSION AUTHORIZATION inventorymantra",
        "ALTER ROLE CURRENT_USER SET search_path = public",
        "SELECT set_config('role', 'inventorymantra', false)",
    ],
)
def test_writes_and_escalation_fail(role: AppRole, statement: str) -> None:
    """Even without our session wrapper, the role's defaults make it read-only and powerless."""
    with psycopg.connect(role_dsn(role), connect_timeout=5, autocommit=True) as conn:
        with pytest.raises(psycopg.Error):
            conn.execute(statement)
        assert conn.execute("SELECT current_user").fetchone() == (
            {"inventory_manager": "rms_im_ro", "procurement_manager": "rms_pm_ro",
             "owner": "rms_owner_ro"}[role],
        )  # fmt: skip


@pytest.mark.parametrize("role", ROLES)
def test_role_defaults(role: AppRole) -> None:
    with psycopg.connect(role_dsn(role), connect_timeout=5, autocommit=True) as conn:
        assert conn.execute("SHOW statement_timeout").fetchone() == ("5s",)
        assert conn.execute("SHOW default_transaction_read_only").fetchone() == ("on",)
        assert conn.execute("SHOW search_path").fetchone() == (VIEW_SCHEMA[role],)
        # unqualified names resolve to the role's own views
        assert conn.execute("SELECT count(*) FROM items").fetchone() == (501,)


def test_statement_timeout_stops_runaway_queries() -> None:
    with (
        readonly_session("inventory_manager", None, TODAY) as conn,
        pytest.raises(psycopg.errors.QueryCanceled),
    ):
        conn.execute("SELECT pg_sleep(6)")


# ------------------------------------------------------------------ column-level visibility
def test_inventory_manager_sees_no_money_vendor_or_people_columns(db: psycopg.Connection) -> None:
    leaks = [(t, c) for t, c in columns(db, "v_im") if IM_FORBIDDEN.search(c)]
    assert leaks == []


def test_procurement_manager_sees_no_sales_or_people_columns(db: psycopg.Connection) -> None:
    leaks = [(t, c) for t, c in columns(db, "v_pm") if PM_FORBIDDEN.search(c)]
    assert leaks == []
    assert all_views(db)["v_pm"].isdisjoint(OWNER_ONLY_VIEWS)
    assert all_views(db)["v_im"].isdisjoint(OWNER_ONLY_VIEWS)


def test_owner_users_view_hides_secrets(db: psycopg.Connection) -> None:
    cols = {c for t, c in columns(db, "v_owner") if t == "users"}
    assert cols.isdisjoint({"password_hash", "token_version"})


# ------------------------------------------------------------------ row-level (my_* views)
@pytest.fixture
def audit_rows(db: psycopg.Connection) -> Iterator[None]:
    """Committed audit rows for PM and Owner (role sessions can't see uncommitted data)."""
    ids = [uuid.uuid4(), uuid.uuid4()]
    for audit_id, role in zip(ids, ["procurement_manager", "owner"], strict=True):
        db.execute(
            """INSERT INTO agent_audit_log (id, operation_id, user_id, role, operation, status)
               VALUES (%s, %s, %s, %s, 'test_op', 'success')""",
            (audit_id, f"test-{audit_id}", USER_ID[role], role),
        )
    yield
    db.execute("DELETE FROM agent_audit_log WHERE id = ANY(%s)", (ids,))


@pytest.mark.usefixtures("audit_rows")
def test_pm_sees_only_own_audit_rows() -> None:
    with readonly_session("procurement_manager", USER_ID["procurement_manager"], TODAY) as conn:
        assert conn.execute("SELECT role FROM my_audit_log").fetchall() == [
            ("procurement_manager",)
        ]
    with readonly_session("procurement_manager", None, TODAY) as conn:  # no user -> no rows
        assert conn.execute("SELECT count(*) FROM my_audit_log").fetchone() == (0,)
    with readonly_session("owner", USER_ID["owner"], TODAY) as conn:
        assert conn.execute("SELECT count(*) FROM audit_log").fetchone() == (2,)


def test_my_stock_requests_are_per_user() -> None:
    with readonly_session("inventory_manager", USER_ID["inventory_manager"], TODAY) as conn:
        assert conn.execute("SELECT count(*) FROM my_stock_requests").fetchone() == (5,)
    with readonly_session("procurement_manager", USER_ID["procurement_manager"], TODAY) as conn:
        assert conn.execute("SELECT count(*) FROM my_stock_requests").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM stock_requests").fetchone() == (5,)


# ------------------------------------------------------------------ view semantics
@pytest.mark.parametrize("session", ["inventory_manager"], indirect=True)
def test_uncategorised_items_are_explicit(session: psycopg.Connection) -> None:
    row = session.execute(
        "SELECT count(*), bool_or(category_recorded) FROM items WHERE category = 'Uncategorised'"
    ).fetchone()
    assert row == (40, False)


@pytest.mark.parametrize("session", ["inventory_manager"], indirect=True)
def test_im_reorder_and_inbound_views(session: psycopg.Connection) -> None:
    row = session.execute("SELECT count(*) FROM reorder_status WHERE status = 'reorder_now'")
    assert row.fetchone() == (15,)
    cursor = session.execute("SELECT * FROM inbound_by_item LIMIT 0")
    cols = [d.name for d in cursor.description or []]
    assert cols == [
        "item_id",
        "item_code",
        "item_name",
        "po_number",
        "po_status",
        "pending_qty",
        "unit",
        "expected_delivery_date",
        "delivery_location",
    ]
    pending = session.execute("SELECT count(*) FROM inbound_by_item").fetchone()
    assert pending is not None and pending[0] > 0


@pytest.mark.parametrize("session", ["procurement_manager"], indirect=True)
def test_overdue_days_use_the_session_business_date(session: psycopg.Connection) -> None:
    row = session.execute(
        "SELECT expected_delivery_date, days_overdue FROM purchase_orders"
        " WHERE po_number = 'PO-2026-0003'"
    ).fetchone()
    assert row == (date(2026, 8, 13), 48)


@pytest.mark.parametrize("session", ["owner"], indirect=True)
def test_owner_margins_by_quarter(session: psycopg.Connection) -> None:
    rows = session.execute("SELECT quarter, margin_pct FROM margins_by_quarter ORDER BY 1")
    assert [(q, float(m)) for q, m in rows.fetchall()] == [
        ("2025-Q4", 23.6), ("2026-Q1", 25.2), ("2026-Q2", 20.9), ("2026-Q3", 24.7)
    ]  # fmt: skip
