"""The seeded database matches the source export (run `make seed` first)."""

from decimal import Decimal

import psycopg
import pytest

from scripts.seed import SEED_DIR, SEED_TABLES, csv_row_count


@pytest.mark.parametrize("table", SEED_TABLES)
def test_row_counts_match_seed_files(db: psycopg.Connection, table: str) -> None:
    row = db.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608 (fixed list)
    assert row is not None and row[0] == csv_row_count(SEED_DIR / f"{table}.csv")


def test_known_facts_from_export(db: psycopg.Connection) -> None:
    facts = db.execute(
        """
        SELECT
          (SELECT sum(total_amount) FROM proc_purchase_orders),
          (SELECT count(*) FROM proc_purchase_orders WHERE status = 'placed'),
          (SELECT count(*) FROM inv_items WHERE category_id IS NULL),
          (SELECT count(*) FROM inv_current_stock WHERE min_stock_level IS NOT NULL),
          (SELECT min(placed_on)::text || '..' || max(placed_on)::text FROM proc_purchase_orders)
        """
    ).fetchone()
    assert facts == (Decimal("5699952.54"), 33, 40, 0, "2026-08-01..2026-09-22")


def test_expected_dates_are_real_json_arrays(db: psycopg.Connection) -> None:
    row = db.execute(
        """
        SELECT count(*) FILTER (WHERE json_typeof(expected_delivery_date) = 'array'),
               count(*) FILTER (WHERE expected_delivery_date IS NOT NULL)
        FROM proc_purchase_orders
        """
    ).fetchone()
    assert row is not None and row[0] == row[1] > 0
    latest = db.execute(
        """
        SELECT (SELECT max(d::date) FROM json_array_elements_text(expected_delivery_date) d)
        FROM proc_purchase_orders WHERE po_number = 'PO-2026-0009'
        """
    ).fetchone()
    assert latest is not None and str(latest[0]) == "2026-08-08"


def test_stock_is_unique_per_item_and_location(db: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.UniqueViolation), db.transaction():
        db.execute(
            """
            INSERT INTO inv_current_stock (id, item_id, location_id, quantity)
            SELECT 'dup-test', item_id, location_id, 0 FROM inv_current_stock LIMIT 1
            """
        )


def test_platform_tables_start_empty(db: psycopg.Connection) -> None:
    for table in ("users", "refresh_tokens", "agent_audit_log"):
        row = db.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
        assert row is not None and row[0] == 0
