"""Baseline: business tables from the source export, plus platform tables (empty).

Column names and types mirror the source database so seeds load unchanged. We add the
primary keys, foreign keys and unique constraints the data already satisfies (verified
in C1.2), so later writes can't break them.

Revision ID: 0001_baseline
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

UPGRADE_SQL = """
-- ---------- item master ----------
CREATE TABLE inv_categories (
    id varchar PRIMARY KEY,
    name varchar NOT NULL,
    short_code varchar,
    position integer,
    created_at timestamp
);

CREATE TABLE inv_sub_categories (
    id varchar PRIMARY KEY,
    name varchar NOT NULL,
    short_code varchar,
    category_id varchar REFERENCES inv_categories(id),
    position integer,
    created_at timestamp
);

CREATE TABLE inv_sub_categories_2 (
    id varchar PRIMARY KEY,
    name varchar NOT NULL,
    short_code varchar,
    sub_category_id varchar REFERENCES inv_sub_categories(id),
    source_id varchar,
    standard text,
    description text,
    naming_convention text,
    convention_notes text,
    examples text,
    position integer,
    created_at timestamp,
    hsn_code varchar,
    nomenclature_fields text,
    nomenclature_version integer,
    cut_shape varchar,
    cut_formula text
);

CREATE TABLE inv_field_definitions (
    id varchar PRIMARY KEY,
    name varchar NOT NULL,
    field_type varchar,
    required boolean,
    options text,
    position integer,
    created_at timestamp,
    updated_at timestamp
);

CREATE TABLE inv_locations (
    id varchar PRIMARY KEY,
    name varchar NOT NULL,
    address text,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamp
);

CREATE TABLE inv_issued_to_targets (
    id varchar PRIMARY KEY,
    name varchar NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamp
);

CREATE TABLE inv_items (
    id varchar PRIMARY KEY,
    item_code varchar NOT NULL UNIQUE,
    name varchar NOT NULL,
    category_id varchar REFERENCES inv_categories(id),
    sub_category_id varchar REFERENCES inv_sub_categories(id),
    sub_category_2_id varchar REFERENCES inv_sub_categories_2(id),
    status varchar NOT NULL DEFAULT 'active',
    unit varchar,
    hsn_code varchar,
    notes text,
    specifications text,
    alt_units text,
    is_spare boolean NOT NULL DEFAULT false
);

-- ---------- stock ----------
CREATE TABLE inv_current_stock (
    id varchar PRIMARY KEY,
    item_id varchar NOT NULL REFERENCES inv_items(id),
    location_id varchar NOT NULL REFERENCES inv_locations(id),
    quantity double precision NOT NULL DEFAULT 0,
    min_stock_level double precision,
    max_stock_level double precision,
    last_transaction_at timestamp,
    updated_at timestamp,
    UNIQUE (item_id, location_id)
);

CREATE TABLE inv_transactions (
    id varchar PRIMARY KEY,
    item_id varchar NOT NULL REFERENCES inv_items(id),
    location_id varchar NOT NULL REFERENCES inv_locations(id),
    transaction_type varchar NOT NULL,
    quantity double precision NOT NULL,
    reference_type varchar,
    reference_id varchar,
    transfer_location_id varchar REFERENCES inv_locations(id),
    unit_cost double precision,
    notes text,
    created_by varchar,
    created_at timestamp NOT NULL,
    currency varchar,
    issued_to_id varchar REFERENCES inv_issued_to_targets(id)
);
CREATE INDEX ix_inv_transactions_item_created ON inv_transactions (item_id, created_at);

-- ---------- procurement ----------
CREATE TABLE proc_vendors (
    id varchar PRIMARY KEY,
    name varchar NOT NULL,
    code varchar,
    city varchar,
    payment_terms varchar,
    is_active boolean NOT NULL DEFAULT true,
    created_by varchar,
    created_at timestamp,
    updated_at timestamp
);

CREATE TABLE proc_purchase_orders (
    id varchar PRIMARY KEY,
    po_number varchar NOT NULL UNIQUE,
    vendor_id varchar REFERENCES proc_vendors(id),
    status varchar NOT NULL,
    placed_on date,
    currency varchar,
    subtotal numeric,
    tax_amount numeric,
    total_amount numeric,
    payment_terms varchar,
    delivery_location_id varchar REFERENCES inv_locations(id),
    ship_to_address text,
    custom_fields text,
    created_by varchar,
    created_at timestamp,
    updated_at timestamp,
    expected_delivery_date json,
    expected_dispatch_date json
);

CREATE TABLE proc_po_lines (
    id varchar PRIMARY KEY,
    po_id varchar NOT NULL REFERENCES proc_purchase_orders(id),
    inv_item_id varchar REFERENCES inv_items(id),
    quantity_ordered numeric NOT NULL,
    quantity_received numeric NOT NULL DEFAULT 0,
    unit varchar,
    unit_price numeric,
    tax_percentage numeric,
    line_subtotal numeric,
    line_tax numeric,
    line_total numeric,
    notes text,
    position integer,
    custom_fields text,
    created_at timestamp,
    updated_at timestamp,
    is_regularised boolean NOT NULL DEFAULT false,
    expected_delivery_date json,
    expected_dispatch_date json
);

CREATE TABLE proc_po_receipts (
    id varchar PRIMARY KEY,
    po_line_id varchar NOT NULL REFERENCES proc_po_lines(id),
    quantity numeric NOT NULL,
    location_id varchar REFERENCES inv_locations(id),
    notes text,
    inv_transaction_id varchar REFERENCES inv_transactions(id),
    received_by varchar,
    received_at timestamp
);

CREATE TABLE proc_po_payment_tranches (
    id varchar PRIMARY KEY,
    po_id varchar NOT NULL REFERENCES proc_purchase_orders(id),
    sequence integer,
    stage varchar,
    percentage numeric,
    amount numeric,
    is_advance boolean,
    blocks_dispatch boolean,
    due_basis varchar,
    due_offset_days integer,
    source varchar,
    cleared_at timestamp,
    cleared_by varchar,
    created_at timestamp,
    updated_at timestamp
);

CREATE TABLE proc_unexpected_receipt_headers (
    id varchar PRIMARY KEY,
    received_by varchar,
    received_at timestamp,
    notes text,
    vendor_name varchar
);

CREATE TABLE proc_unexpected_receipts (
    id varchar PRIMARY KEY,
    inv_item_id varchar REFERENCES inv_items(id),
    quantity numeric NOT NULL,
    location_id varchar REFERENCES inv_locations(id),
    notes text,
    inv_transaction_id varchar REFERENCES inv_transactions(id),
    received_by varchar,
    received_at timestamp,
    resolved_po_id varchar REFERENCES proc_purchase_orders(id),
    resolved_by varchar,
    resolved_at timestamp,
    header_id varchar REFERENCES proc_unexpected_receipt_headers(id)
);

CREATE TABLE proc_po_line_regularisations (
    id varchar PRIMARY KEY,
    po_id varchar REFERENCES proc_purchase_orders(id),
    po_line_id varchar REFERENCES proc_po_lines(id),
    ordered_qty numeric,
    received_qty_before numeric,
    regularised_qty numeric,
    received_qty_after numeric,
    qty_variance numeric,
    unit_price numeric,
    actual_amount numeric,
    expected_amount numeric,
    amount_variance numeric,
    notes text,
    inv_transaction_id varchar REFERENCES inv_transactions(id),
    regularised_by varchar,
    regularised_at timestamp,
    reviewed_by varchar,
    reviewed_at timestamp,
    review_notes text
);

-- ---------- platform (created empty; old rows are not carried over) ----------
CREATE TABLE users (
    id uuid PRIMARY KEY,
    email varchar NOT NULL UNIQUE,
    password_hash varchar NOT NULL,
    full_name varchar NOT NULL,
    role varchar NOT NULL
        CHECK (role IN ('owner', 'procurement_manager', 'inventory_manager')),
    is_active boolean NOT NULL DEFAULT true,
    token_version integer NOT NULL DEFAULT 0,
    created_by uuid REFERENCES users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE refresh_tokens (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id),
    token_hash varchar NOT NULL UNIQUE,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agent_audit_log (
    id uuid PRIMARY KEY,
    operation_id varchar NOT NULL UNIQUE,  -- idempotency key (WRITE_PATH.md §2)
    user_id uuid REFERENCES users(id),
    role varchar NOT NULL,
    operation varchar NOT NULL,
    entity_type varchar,
    entity_id varchar,
    before_state jsonb,
    after_state jsonb,
    status varchar NOT NULL,
    error text,
    thread_id varchar,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);
"""

DOWNGRADE_TABLES = [
    "agent_audit_log",
    "refresh_tokens",
    "users",
    "proc_po_line_regularisations",
    "proc_unexpected_receipts",
    "proc_unexpected_receipt_headers",
    "proc_po_payment_tranches",
    "proc_po_receipts",
    "proc_po_lines",
    "proc_purchase_orders",
    "proc_vendors",
    "inv_transactions",
    "inv_current_stock",
    "inv_items",
    "inv_issued_to_targets",
    "inv_locations",
    "inv_field_definitions",
    "inv_sub_categories_2",
    "inv_sub_categories",
    "inv_categories",
]


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    for table in DOWNGRADE_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
