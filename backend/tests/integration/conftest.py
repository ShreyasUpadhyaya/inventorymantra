"""Write tests run against the seeded database with throwaway items (codes TS-…), so the
real catalogue, stock and answer keys are untouched. Everything they create is removed."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

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


def _cleanup(db: psycopg.Connection, ids: list[str]) -> None:
    db.execute("DELETE FROM agent_audit_log WHERE operation_id LIKE 'test-%'")
    db.execute("DELETE FROM notifications WHERE payload->>'item_id' = ANY(%s)", (ids,))
    for table in ("inv_transactions", "inv_current_stock", "stock_requests", "reorder_policy"):
        db.execute(f"DELETE FROM {table} WHERE item_id = ANY(%s)", (ids,))  # noqa: S608
    db.execute("DELETE FROM inv_items WHERE id = ANY(%s)", (ids,))


@pytest.fixture
def world(db: psycopg.Connection) -> Iterator[World]:
    if get_settings().readonly_db_password is None:
        pytest.skip("READONLY_DB_PASSWORD not set")
    ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, f"test-item/{code}")) for code, _, _ in ITEMS]
    _cleanup(db, ids)  # leftovers from an interrupted run
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
    users = {}
    for email, _, role in DEMO_USERS:
        user = load_user(db, demo_id("user", email))
        assert user is not None, "run make seed first"
        users[role] = user
    yield World(db, ids[0], ids[1], ids[2], loc, users)
    _cleanup(db, ids)
