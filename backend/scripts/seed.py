"""Migrate the database and (re)load business tables from `data/seed/`.

Idempotent: truncates the seeded tables, then COPYs every CSV in dependency order and
checks the row counts. Run from backend/:  uv run python -m scripts.seed
"""

import csv
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql

from app.db.session import psycopg_dsn
from scripts.fabricate import fabricate

BACKEND_DIR = Path(__file__).resolve().parents[1]
SEED_DIR = BACKEND_DIR.parent / "data" / "seed"

# Parents before children, so foreign keys resolve during COPY.
SEED_TABLES: list[str] = [
    "inv_categories",
    "inv_sub_categories",
    "inv_sub_categories_2",
    "inv_field_definitions",
    "inv_locations",
    "inv_issued_to_targets",
    "inv_items",
    "inv_current_stock",
    "inv_transactions",
    "proc_vendors",
    "proc_purchase_orders",
    "proc_po_lines",
    "proc_po_receipts",
    "proc_po_payment_tranches",
    "proc_unexpected_receipt_headers",
    "proc_unexpected_receipts",
    "proc_po_line_regularisations",
]


def migrate() -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "app" / "db" / "migrations"))
    command.upgrade(cfg, "head")


def csv_row_count(path: Path) -> int:
    with path.open(encoding="utf-8", newline="") as f:
        return sum(1 for _ in csv.reader(f)) - 1


def load(conn: psycopg.Connection) -> dict[str, int]:
    tables = sql.SQL(", ").join(sql.Identifier(t) for t in SEED_TABLES)
    conn.execute(sql.SQL("TRUNCATE {} CASCADE").format(tables))
    counts: dict[str, int] = {}
    for table in SEED_TABLES:
        path = SEED_DIR / f"{table}.csv"
        with path.open(encoding="utf-8", newline="") as f:
            header = next(csv.reader(f))
            f.seek(0)
            columns = sql.SQL(", ").join(sql.Identifier(c) for c in header)
            copy_sql = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv, HEADER true)").format(
                sql.Identifier(table), columns
            )
            with conn.cursor().copy(copy_sql) as copy:
                copy.write(f.read())
        loaded = conn.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
        ).fetchone()
        counts[table] = loaded[0] if loaded else 0
        expected = csv_row_count(path)
        if counts[table] != expected:
            raise RuntimeError(f"{table}: loaded {counts[table]} rows, expected {expected}")
    return counts


def main() -> None:
    migrate()
    with psycopg.connect(psycopg_dsn(), connect_timeout=10) as conn:
        counts = load(conn)
        demo = fabricate(conn)
    for table, n in counts.items():
        print(f"{table:34s} {n:>5}")
    print(f"Seeded {len(counts)} tables, {sum(counts.values())} rows.")
    print(
        f"Demo data: {demo.users} users, {demo.reorder_policies} reorder policies, "
        f"{demo.sales_orders} sales orders ({demo.sales_lines} lines), "
        f"{demo.stock_requests} stock requests."
    )
    if demo.generated_password:
        print(
            "\nDemo login password (shown once; set DEMO_PASSWORD in .env to choose your own):"
            f"\n    {demo.generated_password}"
        )
    else:
        print("\nDemo users use DEMO_PASSWORD from .env.")


if __name__ == "__main__":
    main()
