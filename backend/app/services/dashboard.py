"""Role dashboards (C3.3). Every query runs as the caller's read-only role user against that
role's views (app.db.readonly), so a dashboard can never show more than the role may see.

Sections share a generic shape (viz, columns, rows) so the frontend can render dashboards
and chat answers with the same components.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import psycopg
from pydantic import BaseModel

from app.db.readonly import AppRole, readonly_session

Viz = Literal["table", "bar", "line", "pie"]
Format = Literal["count", "inr", "pct", "days"]
TODAY = "current_setting('app.today')::date"


class Kpi(BaseModel):
    id: str
    label: str
    value: float | int | None
    format: Format


class Section(BaseModel):
    id: str
    title: str
    viz: Viz
    x: str | None
    y: list[str]
    columns: list[str]
    rows: list[list[Any]]
    empty_message: str


class DashboardOut(BaseModel):
    role: AppRole
    today: date
    kpis: list[Kpi]
    sections: list[Section]


@dataclass(frozen=True)
class KpiSpec:
    id: str
    label: str
    format: Format
    sql: str


@dataclass(frozen=True)
class SectionSpec:
    id: str
    title: str
    viz: Viz
    x: str | None
    y: tuple[str, ...]
    sql: str
    empty_message: str = "Nothing to show."


# ------------------------------------------------------------------ building blocks
K_OUT_OF_STOCK = KpiSpec(
    "out_of_stock", "Items out of stock (all warehouses)", "count",
    "SELECT count(*) FROM (SELECT item_id FROM stock_by_location GROUP BY item_id"
    " HAVING sum(quantity) <= 0) t",
)  # fmt: skip
K_IN_STOCK = KpiSpec(
    "in_stock", "Items in stock", "count",
    "SELECT count(DISTINCT item_id) FROM stock_by_location WHERE quantity > 0",
)  # fmt: skip
K_REORDER_NOW = KpiSpec(
    "reorder_now", "Reorder now", "count",
    "SELECT count(*) FROM reorder_status WHERE status = 'reorder_now'",
)  # fmt: skip
K_MY_OPEN_REQUESTS = KpiSpec(
    "my_open_requests", "My open stock requests", "count",
    "SELECT count(*) FROM my_stock_requests WHERE status = 'open'",
)  # fmt: skip
K_OPEN_REQUESTS = KpiSpec(
    "open_requests", "Open stock requests", "count",
    "SELECT count(*) FROM stock_requests WHERE status = 'open'",
)  # fmt: skip
K_OPEN_PO_VALUE = KpiSpec(
    "open_po_value", "Open PO value", "inr",
    "SELECT coalesce(sum(total_amount), 0) FROM purchase_orders"
    " WHERE status IN ('placed', 'partial')",
)  # fmt: skip
K_OVERDUE_POS = KpiSpec(
    "overdue_pos", "Overdue POs", "count",
    "SELECT count(*) FROM purchase_orders WHERE days_overdue > 0",
)  # fmt: skip
K_UNPAID = KpiSpec(
    "unpaid_instalments", "Unpaid instalments", "inr",
    "SELECT coalesce(sum(amount), 0) FROM payment_tranches WHERE NOT is_cleared",
)  # fmt: skip
K_REVENUE_YTD = KpiSpec(
    "revenue_ytd", "Revenue this year", "inr",
    f"SELECT coalesce(sum(revenue), 0) FROM sales_orders WHERE status <> 'cancelled'"  # noqa: S608 (TODAY is a constant)
    f" AND order_date >= date_trunc('year', {TODAY})",
)  # fmt: skip
K_LAST_MARGIN = KpiSpec(
    "last_quarter_margin", "Margin, last completed quarter", "pct",
    f"SELECT margin_pct FROM margins_by_quarter"  # noqa: S608 (TODAY is a constant)
    f" WHERE quarter_start < date_trunc('quarter', {TODAY}) ORDER BY quarter_start DESC LIMIT 1",
)  # fmt: skip
K_PENDING_APPROVALS = KpiSpec(
    "pending_approvals", "Waiting for my approval", "count",
    "SELECT count(*) FROM approvals WHERE status = 'pending'",
)  # fmt: skip

S_STOCK_BY_LOCATION = SectionSpec(
    "stock_by_location", "Items in stock by warehouse", "bar", "location_name", ("items",),
    "SELECT location_name, count(*) AS items FROM stock_by_location WHERE quantity > 0"
    " GROUP BY location_name ORDER BY items DESC",
)  # fmt: skip
S_REORDER_IM = SectionSpec(
    "reorder_alerts", "Reorder alerts", "table", None, (),
    "SELECT item_code, item_name, on_hand, unit, days_of_cover, lead_time_days, order_by_date"
    " FROM reorder_status WHERE status = 'reorder_now'"
    " ORDER BY order_by_date NULLS LAST, item_code LIMIT 15",
    "No items need reordering.",
)  # fmt: skip
S_REORDER_PM = SectionSpec(
    "reorder_alerts", "Reorder alerts", "table", None, (),
    "SELECT item_code, item_name, on_hand, on_order, reorder_qty, lead_time_days, order_by_date,"
    " preferred_vendor FROM reorder_status WHERE status = 'reorder_now'"
    " ORDER BY order_by_date NULLS LAST, item_code LIMIT 15",
    "No items need reordering.",
)  # fmt: skip
S_INBOUND = SectionSpec(
    "inbound", "Coming in", "table", None, (),
    "SELECT po_number, item_name, pending_qty, unit, expected_delivery_date FROM inbound_by_item"
    " ORDER BY expected_delivery_date NULLS LAST, po_number LIMIT 10",
    "Nothing is on order.",
)  # fmt: skip
S_RECENT_MOVEMENTS = SectionSpec(
    "recent_movements", "Recent stock movements", "table", None, (),
    "SELECT created_at::date AS date, item_name, movement_type, quantity, unit, location_name,"
    " issued_to FROM stock_movements ORDER BY created_at DESC, movement_id LIMIT 10",
    "No movements recorded.",
)  # fmt: skip
S_MY_REQUESTS = SectionSpec(
    "my_requests", "My stock requests", "table", None, (),
    "SELECT item_name, quantity, unit, needed_by, status, po_number FROM my_stock_requests"
    " ORDER BY created_at DESC LIMIT 10",
    "You haven't raised any stock requests.",
)  # fmt: skip
S_OPEN_REQUESTS = SectionSpec(
    "open_requests", "Open requests from inventory", "table", None, (),
    "SELECT item_name, quantity, unit, needed_by, requested_by_name FROM stock_requests"
    " WHERE status = 'open' ORDER BY needed_by NULLS LAST LIMIT 10",
    "No open requests.",
)  # fmt: skip
S_OVERDUE = SectionSpec(
    "overdue_pos", "Overdue purchase orders", "table", None, (),
    "SELECT po_number, vendor_name, expected_delivery_date, days_overdue, total_amount"
    " FROM purchase_orders WHERE days_overdue > 0 ORDER BY days_overdue DESC, po_number LIMIT 10",
    "No overdue purchase orders.",
)  # fmt: skip
S_PO_STATUS = SectionSpec(
    "po_status", "Purchase orders by status", "pie", "status", ("pos",),
    "SELECT status, count(*) AS pos FROM purchase_orders GROUP BY status ORDER BY pos DESC",
)  # fmt: skip
S_SPEND_BY_MONTH = SectionSpec(
    "spend_by_month", "Procurement spend by month", "line", "month", ("total_spend",),
    "SELECT month, sum(total_spend) AS total_spend FROM spend_by_vendor_month"
    " GROUP BY month ORDER BY month",
)  # fmt: skip
S_TOP_VENDORS = SectionSpec(
    "top_vendors", "Top vendors by spend", "bar", "vendor_name", ("total_spend",),
    "SELECT vendor_name, sum(total_spend) AS total_spend FROM spend_by_vendor_month"
    " GROUP BY vendor_name ORDER BY total_spend DESC, vendor_name LIMIT 5",
)  # fmt: skip
S_MY_APPROVALS = SectionSpec(
    "my_approval_results", "My approval requests", "table", None, (),
    "SELECT po_number, po_total, status, decided_at, note FROM my_approval_results"
    " ORDER BY created_at DESC LIMIT 10",
    "No approval requests yet.",
)  # fmt: skip
S_MARGINS = SectionSpec(
    "margins_by_quarter", "Revenue and margin by quarter", "bar", "quarter",
    ("revenue", "cost", "margin_pct"),
    "SELECT quarter, revenue, cost, gross_profit, margin_pct FROM margins_by_quarter"
    " ORDER BY quarter_start",
)  # fmt: skip
S_PENDING_APPROVALS = SectionSpec(
    "pending_approvals", "Waiting for my approval", "table", None, (),
    "SELECT po_number, vendor_name, po_total, requested_by_name, created_at::date AS requested_on"
    " FROM approvals WHERE status = 'pending' ORDER BY created_at",
    "Nothing is waiting for your approval.",
)  # fmt: skip
S_RECENT_ACTIVITY = SectionSpec(
    "recent_activity", "Recent activity", "table", None, (),
    "SELECT created_at, user_name, role, operation, entity_type, status FROM audit_log"
    " ORDER BY created_at DESC LIMIT 10",
    "No activity recorded yet.",
)  # fmt: skip

LAYOUTS: dict[AppRole, tuple[tuple[KpiSpec, ...], tuple[SectionSpec, ...]]] = {
    "inventory_manager": (
        (K_IN_STOCK, K_OUT_OF_STOCK, K_REORDER_NOW, K_MY_OPEN_REQUESTS),
        (S_REORDER_IM, S_STOCK_BY_LOCATION, S_INBOUND, S_RECENT_MOVEMENTS, S_MY_REQUESTS),
    ),
    "procurement_manager": (
        (K_OPEN_PO_VALUE, K_OVERDUE_POS, K_REORDER_NOW, K_OPEN_REQUESTS, K_UNPAID),
        (
            S_REORDER_PM,
            S_OVERDUE,
            S_OPEN_REQUESTS,
            S_PO_STATUS,
            S_SPEND_BY_MONTH,
            S_TOP_VENDORS,
            S_MY_APPROVALS,
        ),
    ),  # fmt: skip
    "owner": (
        (
            K_REVENUE_YTD,
            K_LAST_MARGIN,
            K_PENDING_APPROVALS,
            K_OPEN_PO_VALUE,
            K_OVERDUE_POS,
            K_REORDER_NOW,
        ),
        (
            S_MARGINS,
            S_PENDING_APPROVALS,
            S_REORDER_PM,
            S_OVERDUE,
            S_SPEND_BY_MONTH,
            S_TOP_VENDORS,
            S_OPEN_REQUESTS,
            S_STOCK_BY_LOCATION,
            S_RECENT_ACTIVITY,
        ),
    ),  # fmt: skip
}


def json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _kpi(conn: psycopg.Connection, spec: KpiSpec) -> Kpi:
    row = conn.execute(spec.sql).fetchone()  # trusted constant SQL, role-scoped connection
    return Kpi(id=spec.id, label=spec.label, value=json_value(row[0]) if row else None,
               format=spec.format)  # fmt: skip


def _section(conn: psycopg.Connection, spec: SectionSpec) -> Section:
    cursor = conn.execute(spec.sql)
    return Section(
        id=spec.id, title=spec.title, viz=spec.viz, x=spec.x, y=list(spec.y),
        columns=[d.name for d in cursor.description or []],
        rows=[[json_value(v) for v in row] for row in cursor.fetchall()],
        empty_message=spec.empty_message,
    )  # fmt: skip


def build(role: AppRole, user_id: UUID, today: date) -> DashboardOut:
    kpis, sections = LAYOUTS[role]
    with readonly_session(role, user_id, today) as conn:
        return DashboardOut(
            role=role,
            today=today,
            kpis=[_kpi(conn, k) for k in kpis],
            sections=[_section(conn, s) for s in sections],
        )
