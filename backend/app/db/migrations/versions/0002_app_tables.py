"""App tables: reorder policy, stock requests, approvals, notifications, demo sales.

Also: pg_trgm (entity matching), the PO number sequence and a PO status check that adds
the statuses the write path needs (WRITE_PATH.md §3).

Revision ID: 0002_app_tables
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_app_tables"
down_revision: str | None = "0001_baseline"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

UPGRADE_SQL = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;

ALTER TABLE proc_purchase_orders ADD CONSTRAINT ck_po_status CHECK (status IN
    ('pending_approval', 'placed', 'partial', 'received', 'rejected', 'cancelled'));

-- Next PO number = PO-<year>-<nextval>; started above the highest existing number (57).
CREATE SEQUENCE po_number_seq START 58;

CREATE TABLE reorder_policy (
    id uuid PRIMARY KEY,
    item_id varchar NOT NULL UNIQUE REFERENCES inv_items(id),
    min_level numeric NOT NULL CHECK (min_level >= 0),
    reorder_qty numeric NOT NULL CHECK (reorder_qty > 0),
    lead_time_days integer NOT NULL CHECK (lead_time_days BETWEEN 1 AND 365),
    safety_stock numeric NOT NULL DEFAULT 0 CHECK (safety_stock >= 0),
    preferred_vendor_id varchar REFERENCES proc_vendors(id),
    source varchar NOT NULL DEFAULT 'app',   -- 'demo' for fabricated rows
    updated_by uuid REFERENCES users(id),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE stock_requests (
    id uuid PRIMARY KEY,
    item_id varchar NOT NULL REFERENCES inv_items(id),
    quantity numeric NOT NULL CHECK (quantity > 0),
    needed_by date,
    note text,
    status varchar NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'ordered', 'fulfilled', 'rejected')),
    requested_by uuid NOT NULL REFERENCES users(id),
    handled_by uuid REFERENCES users(id),
    po_id varchar REFERENCES proc_purchase_orders(id),
    source varchar NOT NULL DEFAULT 'app',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE approvals (
    id uuid PRIMARY KEY,
    entity_type varchar NOT NULL,
    entity_id varchar NOT NULL,
    requested_by uuid NOT NULL REFERENCES users(id),
    status varchar NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    decided_by uuid REFERENCES users(id),
    decided_at timestamptz,
    note text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE notifications (
    id uuid PRIMARY KEY,
    recipient_user_id uuid REFERENCES users(id),
    recipient_role varchar
        CHECK (recipient_role IN ('owner', 'procurement_manager', 'inventory_manager')),
    type varchar NOT NULL,
    title text NOT NULL,
    body text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,   -- built per recipient role; never raw rows
    dedupe_key varchar UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    read_at timestamptz,
    CHECK (recipient_user_id IS NOT NULL OR recipient_role IS NOT NULL)
);

CREATE TABLE sales_orders (
    id uuid PRIMARY KEY,
    so_number varchar NOT NULL UNIQUE,
    customer_name varchar NOT NULL,
    order_date date NOT NULL,
    status varchar NOT NULL CHECK (status IN ('open', 'delivered', 'cancelled')),
    currency varchar NOT NULL DEFAULT 'INR',
    source varchar NOT NULL DEFAULT 'demo'
);

CREATE TABLE sales_order_lines (
    id uuid PRIMARY KEY,
    sales_order_id uuid NOT NULL REFERENCES sales_orders(id) ON DELETE CASCADE,
    item_id varchar NOT NULL REFERENCES inv_items(id),
    quantity numeric NOT NULL CHECK (quantity > 0),
    unit_price numeric NOT NULL CHECK (unit_price > 0),
    unit_cost numeric NOT NULL CHECK (unit_cost > 0),
    line_revenue numeric GENERATED ALWAYS AS (quantity * unit_price) STORED,
    line_cost numeric GENERATED ALWAYS AS (quantity * unit_cost) STORED
);
"""

DOWNGRADE_SQL = """
DROP TABLE IF EXISTS sales_order_lines, sales_orders, notifications, approvals,
    stock_requests, reorder_policy;
DROP SEQUENCE IF EXISTS po_number_seq;
ALTER TABLE proc_purchase_orders DROP CONSTRAINT IF EXISTS ck_po_status;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
