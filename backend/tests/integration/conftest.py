"""Write tests run against the seeded database with throwaway items (codes TS-…), so the
real catalogue, stock and answer keys are untouched. Everything they create is removed."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

import psycopg
import pytest

from app.auth.service import AuthUser, load_user
from app.core.config import get_settings
from scripts.fabricate import DEMO_USERS, demo_id


@dataclass
class World:
    db: psycopg.Connection
    alpha: str  # item id: 10 pcs at 132-1
    beta: str  # item id: no stock anywhere
    inactive: str  # item id: status inactive
    loc: dict[str, str]  # location name -> id
    users: dict[str, AuthUser]  # role -> demo user
    vendor: str  # vendor id: "Zeta Test Supplies", 30% advance, balance against dispatch

    def stock(self, item_id: str, location: str) -> float | None:
        row = self.db.execute(
            "SELECT quantity FROM inv_current_stock WHERE item_id = %s AND location_id = %s",
            (item_id, self.loc[location]),
        ).fetchone()
        return None if row is None else float(row[0])

    def transactions(self, item_id: str) -> list[tuple]:
        return self.db.execute(
            """SELECT transaction_type, quantity, reference_type, reference_id, location_id,
                      transfer_location_id, issued_to_id, unit_cost, created_by
               FROM inv_transactions WHERE item_id = %s ORDER BY created_at, transaction_type""",
            (item_id,),
        ).fetchall()

    def audit(self, operation_id: str) -> tuple | None:
        return self.db.execute(
            """SELECT status, operation, entity_type, entity_id, before_state, after_state, error,
                      role FROM agent_audit_log WHERE operation_id = %s""",
            (operation_id,),
        ).fetchone()


ITEMS = [
    ("TS-ZW-0001", "Zeta Test Widget Alpha", "active"),
    ("TS-ZW-0002", "Zeta Test Widget Beta", "active"),
    ("TS-ZW-0003", "Zeta Test Widget Retired", "inactive"),
]


TEST_POS = """SELECT id FROM proc_purchase_orders WHERE vendor_id IN
                (SELECT id FROM proc_vendors WHERE name LIKE 'Zeta Test%%')"""


def _cleanup(db: psycopg.Connection, ids: list[str], since: datetime | None = None) -> None:
    db.execute("DELETE FROM agent_audit_log WHERE operation_id LIKE 'test-%'")
    if since is not None:  # chat writes made by this test (operation ids "chat:…")
        db.execute("DELETE FROM agent_audit_log WHERE operation_id LIKE 'chat:%%' "
                   "AND created_at >= %s", (since,))  # fmt: skip
    db.execute("DELETE FROM notifications WHERE payload->>'item_id' = ANY(%s)", (ids,))
    db.execute(f"DELETE FROM approvals WHERE entity_id IN ({TEST_POS})")  # noqa: S608
    db.execute("DELETE FROM stock_requests WHERE item_id = ANY(%s)", (ids,))
    db.execute(
        "DELETE FROM proc_po_receipts WHERE po_line_id IN (SELECT id FROM proc_po_lines "  # noqa: S608
        f"WHERE po_id IN ({TEST_POS}))"
    )
    for table in ("proc_po_payment_tranches", "proc_po_lines"):
        db.execute(f"DELETE FROM {table} WHERE po_id IN ({TEST_POS})")  # noqa: S608
    db.execute(f"DELETE FROM proc_purchase_orders WHERE id IN ({TEST_POS})")  # noqa: S608
    headers = db.execute(
        "DELETE FROM proc_unexpected_receipts WHERE inv_item_id = ANY(%s) RETURNING header_id",
        (ids,),
    ).fetchall()
    db.execute("DELETE FROM proc_unexpected_receipt_headers WHERE id = ANY(%s)",
               ([h[0] for h in headers],))  # fmt: skip
    for table in ("inv_transactions", "inv_current_stock", "reorder_policy"):
        db.execute(f"DELETE FROM {table} WHERE item_id = ANY(%s)", (ids,))  # noqa: S608
    db.execute("DELETE FROM inv_items WHERE id = ANY(%s)", (ids,))
    db.execute("DELETE FROM proc_vendors WHERE name LIKE 'Zeta Test%'")
    # PO numbers used by tests are handed out again: next number = highest existing + 1
    db.execute(
        "SELECT setval('po_number_seq', (SELECT coalesce(max(split_part(po_number, '-', 3)::int),"
        " 57) + 1 FROM proc_purchase_orders), false)"
    )


@pytest.fixture
def world(db: psycopg.Connection) -> Iterator[World]:
    if get_settings().readonly_db_password is None:
        pytest.skip("READONLY_DB_PASSWORD not set")
    ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, f"test-item/{code}")) for code, _, _ in ITEMS]
    _cleanup(db, ids)  # leftovers from an interrupted run
    started = db.execute("SELECT now()").fetchone()[0]
    for item_id, (code, name, status) in zip(ids, ITEMS, strict=True):
        db.execute(
            "INSERT INTO inv_items (id, item_code, name, status, unit)"
            " VALUES (%s, %s, %s, %s, 'pcs')",
            (item_id, code, name, status),
        )
    loc = {n: str(i) for i, n in db.execute("SELECT id, name FROM inv_locations").fetchall()}
    db.execute(
        """INSERT INTO inv_current_stock (id, item_id, location_id, quantity, updated_at)
           VALUES (%s, %s, %s, 10, localtimestamp)""",
        (str(uuid.uuid4()), ids[0], loc["132-1"]),
    )
    vendor_id = str(uuid.uuid4())
    db.execute(
        """INSERT INTO proc_vendors (id, name, city, payment_terms, is_active)
           VALUES (%s, 'Zeta Test Supplies', 'Pune', %s, true)""",
        (vendor_id, "30% Advance, Bal Against Dispatch"),
    )
    users = {}
    for email, _, role in DEMO_USERS:
        user = load_user(db, demo_id("user", email))
        assert user is not None, "run make seed first"
        users[role] = user
    yield World(db, ids[0], ids[1], ids[2], loc, users, vendor_id)
    _cleanup(db, ids, since=started)
