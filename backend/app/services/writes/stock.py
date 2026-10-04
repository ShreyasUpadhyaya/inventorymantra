"""Stock writes (WRITE_PATH.md §4): stock movements, transfers and stock requests.

Conventions of the existing data: every stock change is an `inv_transactions` row with a
signed quantity (in > 0, out < 0) plus an update of the item × location row in
`inv_current_stock`. A transfer is a `transfer_out` / `transfer_in` pair whose `transfer_in`
points at the `transfer_out` (reference_id). Stock never goes below zero, and no unit cost is
taken from a manual movement.
"""

import uuid
from datetime import date
from typing import Any, Literal

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from app.agent.formatting import number, show
from app.auth.service import AuthUser
from app.core.config import app_today, business_now
from app.services.writes import resolve
from app.services.writes.framework import (
    Action,
    Outcome,
    Plan,
    WriteError,
    check_quantity,
    register,
)


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def fmt_qty(value: float, unit: str) -> str:
    return f"{number(value)} {unit}"


STOCK = """SELECT location_id, quantity FROM inv_current_stock
           WHERE item_id = %s AND location_id = ANY(%s) ORDER BY id"""
STOCK_LOCKED = STOCK + " FOR UPDATE"


def stock_rows(
    conn: psycopg.Connection, item_id: str, location_ids: list[str], lock: bool
) -> dict[str, float | None]:
    """Quantity per location (None = no row yet), locked in row-id order."""
    found: dict[str, float | None] = dict.fromkeys(location_ids)
    rows = conn.execute(STOCK_LOCKED if lock else STOCK, (item_id, location_ids)).fetchall()
    for location_id, quantity in rows:
        found[str(location_id)] = float(quantity)
    return found


def change_stock(
    conn: psycopg.Connection,
    user: AuthUser,
    *,
    item_id: str,
    location_id: str,
    transaction_type: str,
    quantity: float,
    reference_type: str,
    reference_id: str | None = None,
    transfer_location_id: str | None = None,
    issued_to_id: str | None = None,
    notes: str | None = None,
    unit_cost: float | None = None,
) -> tuple[str, float]:
    """One signed transaction plus the stock row. Returns (transaction id, new quantity)."""
    tx_id = str(uuid.uuid4())
    stamp = business_now()
    conn.execute(
        """INSERT INTO inv_transactions (id, item_id, location_id, transaction_type, quantity,
               reference_type, reference_id, transfer_location_id, issued_to_id, notes,
               unit_cost, currency, created_by, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (tx_id, item_id, location_id, transaction_type, quantity, reference_type, reference_id,
         transfer_location_id, issued_to_id, notes, unit_cost,
         "INR" if unit_cost is not None else None, str(user.id), stamp),
    )  # fmt: skip
    row = conn.execute(
        """INSERT INTO inv_current_stock (id, item_id, location_id, quantity,
               last_transaction_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (item_id, location_id) DO UPDATE
           SET quantity = inv_current_stock.quantity + EXCLUDED.quantity,
               last_transaction_at = EXCLUDED.last_transaction_at,
               updated_at = EXCLUDED.updated_at
           RETURNING quantity""",
        (str(uuid.uuid4()), item_id, location_id, quantity, stamp, stamp),
    ).fetchone()
    assert row is not None
    if row[0] < 0:  # the locks make this unreachable; the check keeps it impossible
        raise WriteError("That would take the stock below zero, so nothing was saved.")
    return tx_id, float(row[0])


def low_stock_event(
    conn: psycopg.Connection, item_id: str, item_name: str, location_name: str, at_location: float
) -> list[dict[str, Any]]:
    """After stock goes out: tell the buyers if the item is now at or below its reorder level."""
    row = conn.execute(
        """SELECT r.min_level, (SELECT coalesce(sum(quantity), 0) FROM inv_current_stock
                                WHERE item_id = r.item_id)
           FROM reorder_policy r WHERE r.item_id = %s""",
        (item_id,),
    ).fetchone()
    if row is None or float(row[1]) > float(row[0]):
        return []
    return [{"event": "low_stock", "data": {
        "item_name": item_name, "location_name": location_name, "quantity": at_location,
        "total_on_hand": float(row[1]), "reorder_point": float(row[0]),
    }}]  # fmt: skip


# ------------------------------------------------------------------ record_stock_movement
class StockMovementArgs(_Args):
    kind: Literal["inbound", "outbound", "issue"]
    item: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=80)
    quantity: float
    issued_to: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=300)


KIND_WORDS = {
    "inbound": ("Add", "to", "added to"),
    "outbound": ("Remove", "from", "removed from"),
    "issue": ("Issue", "from", "issued from"),
}


class RecordStockMovement(Action):
    name = "record_stock_movement"
    entity_type = "stock"
    args_model = StockMovementArgs

    def plan(self, conn: psycopg.Connection, user: AuthUser, args: StockMovementArgs) -> Plan:
        item = resolve.item(conn, user.role, args.item)
        loc = resolve.location(conn, user.role, args.location)
        quantity = check_quantity(args.quantity)
        target = None
        if args.kind == "issue":
            target = resolve.issue_target(conn, args.issued_to or "")
        elif args.issued_to:
            raise WriteError(
                "Only an issue goes to a shop floor or team. Did you mean to issue it?"
            )
        current = stock_rows(conn, item.id, [loc.id], lock=False)[loc.id] or 0.0
        signed = quantity if args.kind == "inbound" else -quantity
        if current + signed < 0:
            raise WriteError(
                f"{loc.name} has only {fmt_qty(current, item.unit)} of {item.name}, "
                f"so {fmt_qty(quantity, item.unit)} can't go out."
            )
        verb, prep, _ = KIND_WORDS[args.kind]
        summary = f"{verb} {fmt_qty(quantity, item.unit)} of {item.name} {prep} {loc.name}"
        if target:
            summary += f" to {target.name}"
        fields = [("Item", f"{item.name} ({item.code})"), ("Warehouse", loc.name),
                  ("Quantity", fmt_qty(quantity, item.unit))]  # fmt: skip
        if target:
            fields.append(("Issued to", target.name))
        if args.note:
            fields.append(("Note", args.note))
        payload = {
            "kind": args.kind, "item_id": item.id, "item_name": item.name, "unit": item.unit,
            "location_id": loc.id, "location_name": loc.name, "quantity": quantity,
            "issued_to_id": target.id if target else None,
            "issued_to_name": target.name if target else None, "note": args.note,
        }  # fmt: skip
        effects = [
            f"{loc.name}: {fmt_qty(current, item.unit)} → {fmt_qty(current + signed, item.unit)}"
        ]
        return Plan(payload=payload, summary=summary + ".", fields=fields, effects=effects)

    def read_state(
        self, conn: psycopg.Connection, payload: dict[str, Any], lock: bool
    ) -> dict[str, Any]:
        rows = stock_rows(conn, payload["item_id"], [payload["location_id"]], lock)
        return {"stock": rows}

    def apply(
        self, conn: psycopg.Connection, user: AuthUser, payload: dict[str, Any],
        state: dict[str, Any],
    ) -> Outcome:  # fmt: skip
        kind = payload["kind"]
        quantity = float(payload["quantity"])
        tx_id, after = change_stock(
            conn, user,
            item_id=payload["item_id"], location_id=payload["location_id"],
            transaction_type="inbound" if kind == "inbound" else "outbound",
            quantity=quantity if kind == "inbound" else -quantity,
            reference_type="warehouse_issue" if kind == "issue" else "manual_adjustment",
            issued_to_id=payload["issued_to_id"], notes=payload["note"],
        )  # fmt: skip
        unit, loc = payload["unit"], payload["location_name"]
        _, _, done = KIND_WORDS[kind]
        message = (f"Done: {fmt_qty(quantity, unit)} of {payload['item_name']} {done} {loc}. "
                   f"{loc} now has {fmt_qty(after, unit)}.")  # fmt: skip
        events = []
        if kind != "inbound":
            events = low_stock_event(conn, payload["item_id"], payload["item_name"], loc, after)
        return Outcome(message=message, entity_id=tx_id,
                       after={"stock": {payload["location_id"]: after}}, events=events)  # fmt: skip


# ------------------------------------------------------------------ transfer_stock
class TransferArgs(_Args):
    item: str = Field(min_length=1, max_length=200)
    from_location: str = Field(min_length=1, max_length=80)
    to_location: str = Field(min_length=1, max_length=80)
    quantity: float
    note: str | None = Field(default=None, max_length=300)


class TransferStock(Action):
    name = "transfer_stock"
    entity_type = "stock"
    args_model = TransferArgs

    def plan(self, conn: psycopg.Connection, user: AuthUser, args: TransferArgs) -> Plan:
        item = resolve.item(conn, user.role, args.item)
        source = resolve.location(conn, user.role, args.from_location)
        dest = resolve.location(conn, user.role, args.to_location)
        if source.id == dest.id:
            raise WriteError("The two warehouses are the same; choose a different destination.")
        quantity = check_quantity(args.quantity)
        rows = stock_rows(conn, item.id, [source.id, dest.id], lock=False)
        have, there = rows[source.id] or 0.0, rows[dest.id] or 0.0
        if quantity > have:
            raise WriteError(
                f"{source.name} has only {fmt_qty(have, item.unit)} of {item.name}, "
                f"so {fmt_qty(quantity, item.unit)} can't be moved."
            )
        payload = {
            "item_id": item.id, "item_name": item.name, "unit": item.unit,
            "from_id": source.id, "from_name": source.name, "to_id": dest.id,
            "to_name": dest.name, "quantity": quantity, "note": args.note,
        }  # fmt: skip
        u = item.unit
        return Plan(
            payload=payload,
            summary=f"Move {fmt_qty(quantity, u)} of {item.name} "
            f"from {source.name} to {dest.name}.",
            fields=[("Item", f"{item.name} ({item.code})"), ("From", source.name),
                    ("To", dest.name), ("Quantity", fmt_qty(quantity, u))]
            + ([("Note", args.note)] if args.note else []),
            effects=[f"{source.name}: {fmt_qty(have, u)} → {fmt_qty(have - quantity, u)}",
                     f"{dest.name}: {fmt_qty(there, u)} → {fmt_qty(there + quantity, u)}"],
        )  # fmt: skip

    def read_state(
        self, conn: psycopg.Connection, payload: dict[str, Any], lock: bool
    ) -> dict[str, Any]:
        return {"stock": stock_rows(conn, payload["item_id"],
                                     [payload["from_id"], payload["to_id"]], lock)}  # fmt: skip

    def apply(
        self, conn: psycopg.Connection, user: AuthUser, payload: dict[str, Any],
        state: dict[str, Any],
    ) -> Outcome:  # fmt: skip
        q = float(payload["quantity"])
        out_id, left = change_stock(
            conn, user, item_id=payload["item_id"], location_id=payload["from_id"],
            transaction_type="transfer_out", quantity=-q, reference_type="transfer",
            transfer_location_id=payload["to_id"], notes=payload["note"],
        )  # fmt: skip
        _, arrived = change_stock(
            conn, user, item_id=payload["item_id"], location_id=payload["to_id"],
            transaction_type="transfer_in", quantity=q, reference_type="transfer",
            reference_id=out_id, transfer_location_id=payload["from_id"], notes=payload["note"],
        )  # fmt: skip
        u = payload["unit"]
        src, dst = payload["from_name"], payload["to_name"]
        message = (
            f"Done: moved {fmt_qty(q, u)} of {payload['item_name']} from {src} to {dst}. "
            f"{src} now has {fmt_qty(left, u)}, {dst} has {fmt_qty(arrived, u)}."
        )
        events = low_stock_event(conn, payload["item_id"], payload["item_name"],
                                  payload["from_name"], left)  # fmt: skip
        return Outcome(message=message, entity_id=out_id,
                       after={"stock": {payload["from_id"]: left, payload["to_id"]: arrived}},
                       events=events)  # fmt: skip


# ------------------------------------------------------------------ raise_stock_request
class StockRequestArgs(_Args):
    item: str = Field(min_length=1, max_length=200)
    quantity: float
    needed_by: date | None = None
    note: str | None = Field(default=None, max_length=300)


class RaiseStockRequest(Action):
    name = "raise_stock_request"
    entity_type = "stock_request"
    args_model = StockRequestArgs

    def plan(self, conn: psycopg.Connection, user: AuthUser, args: StockRequestArgs) -> Plan:
        item = resolve.item(conn, user.role, args.item)
        quantity = check_quantity(args.quantity)
        if args.needed_by and args.needed_by < app_today():
            raise WriteError("The date the stock is needed by is in the past.")
        needed = show("needed_by", args.needed_by.isoformat()) if args.needed_by else None
        payload = {
            "item_id": item.id, "item_name": item.name, "unit": item.unit, "quantity": quantity,
            "needed_by": args.needed_by.isoformat() if args.needed_by else None,
            "note": args.note,
        }  # fmt: skip
        summary = f"Ask procurement for {fmt_qty(quantity, item.unit)} of {item.name}"
        summary += f", needed by {needed}." if needed else "."
        fields = [
            ("Item", f"{item.name} ({item.code})"),
            ("Quantity", fmt_qty(quantity, item.unit)),
        ]
        if needed:
            fields.append(("Needed by", needed))
        if args.note:
            fields.append(("Note", args.note))
        return Plan(payload=payload, summary=summary, fields=fields,
                    effects=["A new open request for the procurement team."])  # fmt: skip

    def read_state(
        self, conn: psycopg.Connection, payload: dict[str, Any], lock: bool
    ) -> dict[str, Any]:
        return {}  # a new row: nothing existing can go stale

    def apply(
        self, conn: psycopg.Connection, user: AuthUser, payload: dict[str, Any],
        state: dict[str, Any],
    ) -> Outcome:  # fmt: skip
        request_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO stock_requests (id, item_id, quantity, needed_by, note, status,
                   requested_by) VALUES (%s, %s, %s, %s, %s, 'open', %s)""",
            (request_id, payload["item_id"], payload["quantity"], payload["needed_by"],
             payload["note"], user.id),
        )  # fmt: skip
        q = fmt_qty(float(payload["quantity"]), payload["unit"])
        event = {"event": "stock_request_raised", "data": {
            "item_name": payload["item_name"], "quantity": payload["quantity"],
            "unit": payload["unit"], "needed_by": payload["needed_by"],
            "requested_by_name": user.full_name,
        }}  # fmt: skip
        return Outcome(
            message=f"Done: your request for {q} of {payload['item_name']} has been sent to "
            "procurement.",
            entity_id=request_id, after={"status": "open"}, events=[event],
        )  # fmt: skip


for _action in (RecordStockMovement(), TransferStock(), RaiseStockRequest()):
    register(_action)
