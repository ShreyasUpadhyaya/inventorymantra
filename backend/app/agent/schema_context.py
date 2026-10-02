"""The `{schema}` block for the SQL prompt: the caller's role views only.

Columns are read live from the role's own view schema (v_im / v_pm / v_owner), so the
prompt can never describe a view the role can't query. Descriptions add the meaning a
column name alone doesn't carry (units, conventions, how dates are computed).
"""

from functools import lru_cache

import psycopg

from app.db.readonly import VIEW_SCHEMA, AppRole

VIEW_NOTES: dict[str, str] = {
    "items": "item master; category is 'Uncategorised' when category_recorded is false",
    "locations": "warehouses (location_name like '132-1', 'C-84')",
    "issue_targets": "shop floors / teams that stock is issued to",
    "stock_by_location": "current on-hand quantity per item per warehouse (the source of truth)",
    "stock_movements": "movement history: movement_type in inbound, outbound, transfer_in, "
    "transfer_out, receipt; outbound quantities are negative; issues have issued_to",
    "inbound_by_item": "open purchase-order lines still to arrive (pending_qty > 0); "
    "expected_delivery_date is the latest recorded expected date (NULL if none recorded)",
    "my_stock_requests": "stock requests raised by the signed-in user",
    "stock_requests": "all stock requests (status open, ordered, fulfilled, rejected)",
    "reorder_status": "reorder maths per item: avg_daily_use_90d, days_of_cover, reorder_point, "
    "order_by_date; status in reorder_now, below_min, ok",
    "vendors": "suppliers",
    "purchase_orders": "PO headers; status in pending_approval, placed, partial, received, "
    "rejected, cancelled; expected_delivery_date = latest recorded expected date; "
    "days_overdue > 0 when an open PO is past that date",
    "po_lines": "PO lines with prices; pending_qty = ordered - received",
    "po_receipts": "goods received against PO lines",
    "payment_tranches": "PO payment instalments; is_cleared false = unpaid",
    "unexpected_receipts": "deliveries received without a PO; is_resolved false = not matched",
    "regularisations": "quantity/amount corrections on PO lines",
    "spend_by_vendor_month": "PO value per vendor per month (placed, partial, received POs)",
    "my_approval_results": "approval requests raised by the signed-in user and their outcome",
    "my_audit_log": "actions performed by the signed-in user",
    "sales_orders": "sales orders with revenue, cost, gross_profit and margin_pct per order",
    "sales_lines": "sales order lines (Finished Goods)",
    "margins_by_quarter": "revenue, cost, gross_profit, margin_pct per quarter "
    "(quarter like '2026-Q2'), cancelled orders excluded",
    "approvals": "all approval requests (status pending, approved, rejected)",
    "users": "system users (no passwords)",
    "audit_log": "every recorded action by every user",
}


@lru_cache
def allowed_views(role: AppRole) -> frozenset[str]:
    return frozenset(_columns(role))


def _columns(role: AppRole) -> dict[str, list[str]]:
    from app.db.session import psycopg_dsn

    with psycopg.connect(psycopg_dsn(), connect_timeout=5) as conn:
        rows = conn.execute(
            """SELECT table_name, column_name FROM information_schema.columns
               WHERE table_schema = %s ORDER BY table_name, ordinal_position""",
            (VIEW_SCHEMA[role],),
        ).fetchall()
    views: dict[str, list[str]] = {}
    for table, column in rows:
        views.setdefault(table, []).append(column)
    return views


@lru_cache
def schema_block(role: AppRole) -> str:
    lines = []
    for view, columns in _columns(role).items():
        note = VIEW_NOTES.get(view)
        lines.append(f"  {view}({', '.join(columns)})" + (f"  -- {note}" if note else ""))
    return "\n".join(lines)
