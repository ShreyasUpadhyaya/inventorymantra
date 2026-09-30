"""Per-role view schemas and read-only DB roles (DECISION D-004; CLAUDE.md §5).

Each app role gets one schema of views exposing only the columns it may see:
  v_im    -> rms_im_ro      (Inventory Manager)
  v_pm    -> rms_pm_ro      (Procurement Manager)
  v_owner -> rms_owner_ro   (Owner)
A DB role can use only its own schema. It has no privileges on base tables, its search_path
is its schema, and every transaction is read-only with a 5 s statement timeout. The roles
are created NOLOGIN here (no secrets in migrations); scripts/db_roles.py enables login with
READONLY_DB_PASSWORD from the environment.

Per-request context comes from transaction settings that the API sets:
  app.user_id  -> used by my_* views (security_barrier; no user -> no rows)
  app.today    -> business date for overdue / reorder maths (falls back to current_date)

Revision ID: 0003_role_views
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_role_views"
down_revision: str | None = "0002_app_tables"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

TODAY = "coalesce(nullif(current_setting('app.today', true), '')::date, current_date)"
CURRENT_USER_ID = "nullif(current_setting('app.user_id', true), '')::uuid"


def latest(col: str) -> str:
    """Latest date in a JSON array column of dates (NULL if absent or not an array)."""
    return (
        f"(SELECT max(d::date) FROM json_array_elements_text("
        f"CASE WHEN json_typeof({col}) = 'array' THEN {col} END) AS d)"
    )


PO_EXPECTED = latest("p.expected_delivery_date")
LINE_EXPECTED = f"coalesce({latest('l.expected_delivery_date')}, {PO_EXPECTED})"
OPEN_PO = "p.status IN ('placed', 'partial')"

# ---------------------------------------------------------------- view definitions
# {s} is replaced by the target schema. Keys ending in a variant suffix are alternatives
# of the same public view name (e.g. stock_movements without / with unit cost).
VIEWS: dict[str, str] = {}

VIEWS["items"] = """
SELECT i.id AS item_id, i.item_code, i.name AS item_name,
       coalesce(c.name, 'Uncategorised') AS category,
       (i.category_id IS NOT NULL) AS category_recorded,
       sc.name AS sub_category, sc2.name AS item_type,
       i.unit, i.status, i.is_spare, i.hsn_code, i.notes
FROM public.inv_items i
LEFT JOIN public.inv_categories c ON c.id = i.category_id
LEFT JOIN public.inv_sub_categories sc ON sc.id = i.sub_category_id
LEFT JOIN public.inv_sub_categories_2 sc2 ON sc2.id = i.sub_category_2_id
"""

VIEWS["locations"] = """
SELECT id AS location_id, name AS location_name, address, is_active
FROM public.inv_locations
"""

VIEWS["issue_targets"] = """
SELECT id AS issue_target_id, name AS issue_target_name, is_active
FROM public.inv_issued_to_targets
"""

VIEWS["stock_by_location"] = """
SELECT s.item_id, i.item_code, i.name AS item_name,
       coalesce(c.name, 'Uncategorised') AS category,
       s.location_id, l.name AS location_name, s.quantity, i.unit,
       s.last_transaction_at AS last_movement_at
FROM public.inv_current_stock s
JOIN public.inv_items i ON i.id = s.item_id
JOIN public.inv_locations l ON l.id = s.location_id
LEFT JOIN public.inv_categories c ON c.id = i.category_id
"""

_MOVEMENTS = """
SELECT t.id AS movement_id, t.created_at, t.item_id, i.item_code, i.name AS item_name,
       l.name AS location_name, t.transaction_type AS movement_type, t.quantity, i.unit,
       t.reference_type, tl.name AS transfer_location_name, it.name AS issued_to,
       t.notes, coalesce(u.full_name, 'Legacy user') AS recorded_by{cost}
FROM public.inv_transactions t
JOIN public.inv_items i ON i.id = t.item_id
JOIN public.inv_locations l ON l.id = t.location_id
LEFT JOIN public.inv_locations tl ON tl.id = t.transfer_location_id
LEFT JOIN public.inv_issued_to_targets it ON it.id = t.issued_to_id
LEFT JOIN public.users u ON u.id::text = t.created_by
"""
VIEWS["stock_movements:no_cost"] = _MOVEMENTS.replace("{cost}", "")
VIEWS["stock_movements:with_cost"] = _MOVEMENTS.replace("{cost}", ", t.unit_cost, t.currency")

VIEWS["inbound_by_item"] = f"""
SELECT l.inv_item_id AS item_id, i.item_code, i.name AS item_name, p.po_number,
       p.status AS po_status, l.quantity_ordered - l.quantity_received AS pending_qty, i.unit,
       {LINE_EXPECTED} AS expected_delivery_date, loc.name AS delivery_location
FROM public.proc_po_lines l
JOIN public.proc_purchase_orders p ON p.id = l.po_id
JOIN public.inv_items i ON i.id = l.inv_item_id
LEFT JOIN public.inv_locations loc ON loc.id = p.delivery_location_id
WHERE {OPEN_PO} AND l.quantity_ordered > l.quantity_received
"""

_REQUESTS = """
SELECT r.id AS request_id, r.item_id, i.item_code, i.name AS item_name, r.quantity, i.unit,
       r.needed_by, r.note, r.status, rq.full_name AS requested_by_name,
       h.full_name AS handled_by_name, p.po_number, r.created_at, r.updated_at
FROM public.stock_requests r
JOIN public.inv_items i ON i.id = r.item_id
JOIN public.users rq ON rq.id = r.requested_by
LEFT JOIN public.users h ON h.id = r.handled_by
LEFT JOIN public.proc_purchase_orders p ON p.id = r.po_id
"""
VIEWS["stock_requests"] = _REQUESTS
VIEWS["my_stock_requests"] = _REQUESTS + f"WHERE r.requested_by = {CURRENT_USER_ID}\n"

_REORDER = f"""
WITH today AS (SELECT {TODAY} AS d),
usage AS (
    SELECT t.item_id, sum(-t.quantity) / 90.0 AS avg_daily_use
    FROM public.inv_transactions t CROSS JOIN today
    WHERE t.transaction_type = 'outbound'
      AND t.created_at >= today.d - 90 AND t.created_at < today.d + 1
    GROUP BY t.item_id
),
on_hand AS (SELECT item_id, sum(quantity) AS q FROM public.inv_current_stock GROUP BY item_id),
on_order AS (
    SELECT l.inv_item_id AS item_id, sum(l.quantity_ordered - l.quantity_received) AS q
    FROM public.proc_po_lines l JOIN public.proc_purchase_orders p ON p.id = l.po_id
    WHERE {OPEN_PO} GROUP BY l.inv_item_id
),
calc AS (
    SELECT r.*, coalesce(oh.q, 0)::numeric AS on_hand, coalesce(oo.q, 0)::numeric AS on_order,
           coalesce(u.avg_daily_use, 0)::numeric AS daily, today.d AS today
    FROM public.reorder_policy r CROSS JOIN today
    LEFT JOIN usage u ON u.item_id = r.item_id
    LEFT JOIN on_hand oh ON oh.item_id = r.item_id
    LEFT JOIN on_order oo ON oo.item_id = r.item_id
)
SELECT c.item_id, i.item_code, i.name AS item_name, i.unit,
       c.on_hand, c.on_order, c.min_level, c.reorder_qty, c.lead_time_days, c.safety_stock,
       round(c.daily, 2) AS avg_daily_use_90d,
       CASE WHEN c.daily > 0 THEN round(c.on_hand / c.daily, 1) END AS days_of_cover,
       round(c.daily * c.lead_time_days + c.safety_stock, 1) AS reorder_point,
       CASE WHEN c.daily > 0
            THEN c.today + floor(c.on_hand / c.daily)::int - c.lead_time_days END AS order_by_date,
       CASE WHEN c.on_hand + c.on_order <= c.daily * c.lead_time_days + c.safety_stock
                 THEN 'reorder_now'
            WHEN c.on_hand < c.min_level THEN 'below_min'
            ELSE 'ok' END AS status{{vendor}}
FROM calc c
JOIN public.inv_items i ON i.id = c.item_id
LEFT JOIN public.proc_vendors v ON v.id = c.preferred_vendor_id
"""
VIEWS["reorder_status:no_vendor"] = _REORDER.replace("{vendor}", "")
VIEWS["reorder_status:with_vendor"] = _REORDER.replace("{vendor}", ", v.name AS preferred_vendor")

VIEWS["vendors"] = """
SELECT id AS vendor_id, name AS vendor_name, code, city, payment_terms, is_active
FROM public.proc_vendors
"""

VIEWS["purchase_orders"] = f"""
SELECT p.id AS po_id, p.po_number, p.vendor_id, v.name AS vendor_name, p.status, p.placed_on,
       p.currency, p.subtotal, p.tax_amount, p.total_amount, p.payment_terms,
       loc.name AS delivery_location,
       {PO_EXPECTED} AS expected_delivery_date,
       {latest("p.expected_dispatch_date")} AS expected_dispatch_date,
       CASE WHEN {OPEN_PO} AND {PO_EXPECTED} < {TODAY}
            THEN {TODAY} - {PO_EXPECTED} END AS days_overdue,
       coalesce(u.full_name, 'Legacy user') AS created_by_name, p.created_at
FROM public.proc_purchase_orders p
LEFT JOIN public.proc_vendors v ON v.id = p.vendor_id
LEFT JOIN public.inv_locations loc ON loc.id = p.delivery_location_id
LEFT JOIN public.users u ON u.id::text = p.created_by
"""

VIEWS["po_lines"] = f"""
SELECT l.id AS line_id, l.po_id, p.po_number, p.status AS po_status, v.name AS vendor_name,
       l.inv_item_id AS item_id, i.item_code, i.name AS item_name,
       l.quantity_ordered, l.quantity_received,
       l.quantity_ordered - l.quantity_received AS pending_qty, l.unit,
       l.unit_price, l.tax_percentage, l.line_subtotal, l.line_tax, l.line_total,
       {LINE_EXPECTED} AS expected_delivery_date, l.is_regularised, p.placed_on
FROM public.proc_po_lines l
JOIN public.proc_purchase_orders p ON p.id = l.po_id
LEFT JOIN public.proc_vendors v ON v.id = p.vendor_id
LEFT JOIN public.inv_items i ON i.id = l.inv_item_id
"""

VIEWS["po_receipts"] = """
SELECT r.id AS receipt_id, p.po_number, v.name AS vendor_name, i.item_code,
       i.name AS item_name, r.quantity, loc.name AS location_name, r.received_at, r.notes,
       coalesce(u.full_name, 'Legacy user') AS received_by_name
FROM public.proc_po_receipts r
JOIN public.proc_po_lines l ON l.id = r.po_line_id
JOIN public.proc_purchase_orders p ON p.id = l.po_id
LEFT JOIN public.proc_vendors v ON v.id = p.vendor_id
LEFT JOIN public.inv_items i ON i.id = l.inv_item_id
LEFT JOIN public.inv_locations loc ON loc.id = r.location_id
LEFT JOIN public.users u ON u.id::text = r.received_by
"""

VIEWS["payment_tranches"] = """
SELECT t.id AS tranche_id, p.po_number, v.name AS vendor_name, t.sequence, t.stage,
       t.percentage, coalesce(t.amount, round(p.total_amount * t.percentage / 100, 2)) AS amount,
       t.is_advance, t.blocks_dispatch, t.due_basis, t.due_offset_days,
       (t.cleared_at IS NOT NULL) AS is_cleared, t.cleared_at
FROM public.proc_po_payment_tranches t
JOIN public.proc_purchase_orders p ON p.id = t.po_id
LEFT JOIN public.proc_vendors v ON v.id = p.vendor_id
"""

VIEWS["unexpected_receipts"] = """
SELECT r.id AS receipt_id, r.received_at, h.vendor_name, h.notes AS invoice_note,
       i.item_code, i.name AS item_name, r.quantity, loc.name AS location_name,
       (r.resolved_po_id IS NOT NULL) AS is_resolved, p.po_number AS resolved_po_number
FROM public.proc_unexpected_receipts r
LEFT JOIN public.proc_unexpected_receipt_headers h ON h.id = r.header_id
LEFT JOIN public.inv_items i ON i.id = r.inv_item_id
LEFT JOIN public.inv_locations loc ON loc.id = r.location_id
LEFT JOIN public.proc_purchase_orders p ON p.id = r.resolved_po_id
"""

VIEWS["regularisations"] = """
SELECT g.id AS regularisation_id, p.po_number, i.item_code, i.name AS item_name,
       g.ordered_qty, g.regularised_qty, g.qty_variance, g.unit_price, g.actual_amount,
       g.expected_amount, g.amount_variance, g.regularised_at,
       (g.reviewed_at IS NOT NULL) AS is_reviewed, g.notes
FROM public.proc_po_line_regularisations g
LEFT JOIN public.proc_purchase_orders p ON p.id = g.po_id
LEFT JOIN public.proc_po_lines l ON l.id = g.po_line_id
LEFT JOIN public.inv_items i ON i.id = l.inv_item_id
"""

VIEWS["spend_by_vendor_month"] = """
SELECT date_trunc('month', p.placed_on)::date AS month, v.name AS vendor_name,
       count(*) AS po_count, sum(p.total_amount) AS total_spend
FROM public.proc_purchase_orders p
LEFT JOIN public.proc_vendors v ON v.id = p.vendor_id
WHERE p.status IN ('placed', 'partial', 'received')
GROUP BY 1, 2
"""

_APPROVALS = """
SELECT a.id AS approval_id, a.entity_type, p.po_number, p.total_amount AS po_total,
       v.name AS vendor_name, a.status, rq.full_name AS requested_by_name,
       d.full_name AS decided_by_name, a.decided_at, a.note, a.created_at
FROM public.approvals a
JOIN public.users rq ON rq.id = a.requested_by
LEFT JOIN public.users d ON d.id = a.decided_by
LEFT JOIN public.proc_purchase_orders p
       ON a.entity_type = 'purchase_order' AND p.id = a.entity_id
LEFT JOIN public.proc_vendors v ON v.id = p.vendor_id
"""
VIEWS["approvals"] = _APPROVALS
VIEWS["my_approval_results"] = _APPROVALS + f"WHERE a.requested_by = {CURRENT_USER_ID}\n"

_AUDIT = """
SELECT l.id AS audit_id, l.created_at, l.completed_at, u.full_name AS user_name, l.role,
       l.operation, l.entity_type, l.entity_id, l.status, l.error,
       l.before_state, l.after_state
FROM public.agent_audit_log l
LEFT JOIN public.users u ON u.id = l.user_id
"""
VIEWS["audit_log"] = _AUDIT
VIEWS["my_audit_log"] = _AUDIT + f"WHERE l.user_id = {CURRENT_USER_ID}\n"

VIEWS["users"] = """
SELECT u.id AS user_id, u.email, u.full_name, u.role, u.is_active, u.created_at,
       c.full_name AS created_by_name
FROM public.users u
LEFT JOIN public.users c ON c.id = u.created_by
"""

_SALES_LINES = """
SELECT o.so_number, o.order_date, o.customer_name, o.status AS order_status,
       i.item_code, i.name AS item_name, l.quantity, l.unit_price, l.unit_cost,
       l.line_revenue, l.line_cost, l.line_revenue - l.line_cost AS line_gross_profit
FROM public.sales_order_lines l
JOIN public.sales_orders o ON o.id = l.sales_order_id
JOIN public.inv_items i ON i.id = l.item_id
"""
VIEWS["sales_lines"] = _SALES_LINES

VIEWS["sales_orders"] = """
SELECT o.so_number, o.customer_name, o.order_date, o.status, o.currency,
       sum(l.line_revenue) AS revenue, sum(l.line_cost) AS cost,
       sum(l.line_revenue) - sum(l.line_cost) AS gross_profit,
       round(100 * (sum(l.line_revenue) - sum(l.line_cost)) / nullif(sum(l.line_revenue), 0), 1)
           AS margin_pct
FROM public.sales_orders o
JOIN public.sales_order_lines l ON l.sales_order_id = o.id
GROUP BY o.id
"""

VIEWS["margins_by_quarter"] = """
SELECT date_trunc('quarter', o.order_date)::date AS quarter_start,
       to_char(o.order_date, 'YYYY-"Q"Q') AS quarter,
       count(DISTINCT o.id) AS orders,
       sum(l.line_revenue) AS revenue, sum(l.line_cost) AS cost,
       sum(l.line_revenue) - sum(l.line_cost) AS gross_profit,
       round(100 * (sum(l.line_revenue) - sum(l.line_cost)) / nullif(sum(l.line_revenue), 0), 1)
           AS margin_pct
FROM public.sales_orders o
JOIN public.sales_order_lines l ON l.sales_order_id = o.id
WHERE o.status <> 'cancelled'
GROUP BY 1, 2
"""

# ---------------------------------------------------------------- role -> views
IM_VIEWS = [
    "items",
    "locations",
    "issue_targets",
    "stock_by_location",
    "stock_movements:no_cost",
    "inbound_by_item",
    "my_stock_requests",
    "reorder_status:no_vendor",
]
PM_VIEWS = [
    "items",
    "locations",
    "issue_targets",
    "stock_by_location",
    "stock_movements:with_cost",
    "inbound_by_item",
    "my_stock_requests",
    "stock_requests",
    "reorder_status:with_vendor",
    "vendors",
    "purchase_orders",
    "po_lines",
    "po_receipts",
    "payment_tranches",
    "unexpected_receipts",
    "regularisations",
    "spend_by_vendor_month",
    "my_approval_results",
    "my_audit_log",
]
OWNER_VIEWS = [v for v in PM_VIEWS if not v.startswith("my_")] + [
    "sales_orders",
    "sales_lines",
    "margins_by_quarter",
    "approvals",
    "users",
    "audit_log",
]
ROLES = {  # schema: (db role, views)
    "v_im": ("rms_im_ro", IM_VIEWS),
    "v_pm": ("rms_pm_ro", PM_VIEWS),
    "v_owner": ("rms_owner_ro", OWNER_VIEWS),
}


def upgrade() -> None:
    # Nobody but the owner/migrator may use the public schema (base tables live there).
    op.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    for schema, (role, views) in ROLES.items():
        op.execute(f"""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    CREATE ROLE {role} NOLOGIN;
                END IF;
            END $$""")
        op.execute(f"""
            ALTER ROLE {role} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION
                CONNECTION LIMIT 20""")
        op.execute(f"ALTER ROLE {role} SET search_path = {schema}")
        op.execute(f"ALTER ROLE {role} SET default_transaction_read_only = on")
        op.execute(f"ALTER ROLE {role} SET statement_timeout = '5s'")
        op.execute(f"ALTER ROLE {role} SET idle_in_transaction_session_timeout = '30s'")
        op.execute(f"CREATE SCHEMA {schema}")
        op.execute(f"REVOKE ALL ON SCHEMA {schema} FROM PUBLIC")
        for key in views:
            name = key.split(":")[0]
            barrier = " WITH (security_barrier)" if name.startswith("my_") else ""
            op.execute(f"CREATE VIEW {schema}.{name}{barrier} AS {VIEWS[key]}")
        op.execute(f"GRANT USAGE ON SCHEMA {schema} TO {role}")
        op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {role}")


def downgrade() -> None:
    for schema, (role, _) in ROLES.items():
        op.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        op.execute(f"DROP ROLE IF EXISTS {role}")
    op.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
