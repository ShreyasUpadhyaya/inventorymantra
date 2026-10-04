"""Procurement writes (WRITE_PATH.md §4): PO receipts, unplanned deliveries, purchase
orders (create, change date, cancel), payment instalments, reorder levels and vendors.

The server computes every stored number (line totals, tax, PO total, instalment amounts,
unit cost of a receipt) and picks every id; the request only names things. A receipt never
shows a price or vendor in its preview, because the Inventory Manager may record one.
"""

import re
import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import psycopg
from psycopg.types.json import Json
from pydantic import BaseModel, ConfigDict, Field

from app.agent.formatting import inr, show
from app.auth.service import AuthUser
from app.core.config import app_today, business_now, get_settings
from app.policy import permissions
from app.services.writes import resolve
from app.services.writes.framework import (
    Action,
    Ambiguous,
    Outcome,
    Plan,
    WriteError,
    check_quantity,
    register,
)
from app.services.writes.stock import change_stock, fmt_qty, stock_rows

MAX_LINES = 20
CENT = Decimal("0.01")
OPEN_FOR_RECEIPT = ("placed", "partial")
OPEN_FOR_CHANGE = ("pending_approval", "placed", "partial")


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _day(value: str | date | None) -> str:
    return show("date", str(value)) if value else "not recorded"


def _future(value: date, what: str) -> date:
    if value < app_today():
        raise WriteError(f"The {what} is in the past.")
    return value


# ------------------------------------------------------------------ payment terms
def _tranche(
    stage: str, pct: int, advance: bool, blocks: bool, basis: str, days: int | None
) -> dict[str, Any]:
    return {"stage": stage, "percentage": pct, "is_advance": advance, "blocks_dispatch": blocks,
            "due_basis": basis, "due_offset_days": days}  # fmt: skip


def tranches_from_terms(terms: str | None) -> list[dict[str, Any]]:
    """Instalments from the vendor's payment terms text, as recorded in the existing data
    ("50% Advance & 50% Against Dispatch", "30 Days Credit", "10 Days PDC", …). Unknown
    terms become one instalment "As per terms"."""
    text = (terms or "").lower()
    adv = re.search(r"(\d+)\s*%\s*(?:payment\s*)?(?:adv|adavcne)", text)
    days = re.search(r"(\d+)\s*days?", text)
    advance = int(adv.group(1)) if adv else 0

    def balance(pct: int) -> dict[str, Any] | None:
        if days and "pdc" in text:
            n = int(days.group(1))
            return _tranche(f"{n} days PDC against dispatch", pct, False, False, "dispatch", n)
        if days:
            n = int(days.group(1))
            return _tranche(f"{n} days credit", pct, False, False, "credit", n)
        if "dispatch" in text:
            return _tranche("Against dispatch", pct, False, True, "dispatch", 0)
        return None

    first = _tranche("Advance", advance, True, True, "advance", None)
    if advance >= 100:
        return [{**first, "percentage": 100}]
    rest = balance(100 - advance)
    if rest and advance:
        return [first, rest]
    if rest:
        return [rest]
    return [_tranche("As per terms", 100, False, False, "terms", None)]


def _terms_text(tranches: list[dict[str, Any]]) -> str:
    return ", ".join(f"{t['percentage']}% {t['stage'].lower()}" for t in tranches)


# ------------------------------------------------------------------ create_purchase_order
class LineArgs(_Args):
    item: str = Field(min_length=1, max_length=200)
    quantity: float
    unit_price: float
    tax_percentage: float = 0


class CreatePOArgs(_Args):
    vendor: str = Field(min_length=1, max_length=120)
    lines: list[LineArgs] = Field(min_length=1)
    expected_delivery_date: date | None = None
    delivery_location: str | None = Field(default=None, max_length=80)


REQUESTS = """SELECT id::text, status, po_id FROM stock_requests
              WHERE id = ANY(%s::uuid[]) ORDER BY id"""
REQUESTS_LOCKED = REQUESTS + " FOR UPDATE"


class CreatePurchaseOrder(Action):
    name = "create_purchase_order"
    entity_type = "purchase_order"
    args_model = CreatePOArgs

    def plan(self, conn: psycopg.Connection, user: AuthUser, args: CreatePOArgs) -> Plan:
        if len(args.lines) > MAX_LINES:
            raise WriteError(f"A purchase order can have at most {MAX_LINES} lines.")
        vendor = resolve.vendor(conn, user.role, args.vendor)
        loc = resolve.location(conn, user.role, args.delivery_location, "delivery_location") \
            if args.delivery_location else None  # fmt: skip
        expected = _future(args.expected_delivery_date, "expected delivery date") \
            if args.expected_delivery_date else None  # fmt: skip
        lines: list[dict[str, Any]] = []
        seen: set[str] = set()
        subtotal = tax = Decimal(0)
        for index, line in enumerate(args.lines):
            item = resolve.item(conn, user.role, line.item, f"lines.{index}.item")
            if item.id in seen:
                raise WriteError(f"{item.name} is on two lines; put the total on one line.")
            seen.add(item.id)
            qty = check_quantity(line.quantity)
            if line.unit_price <= 0:
                raise WriteError(f"The unit price of {item.name} must be more than zero.")
            if not 0 <= line.tax_percentage <= 28:
                raise WriteError("Tax must be between 0% and 28%.")
            price = _money(Decimal(str(line.unit_price)))
            line_sub = _money(Decimal(str(qty)) * price)
            line_tax = _money(line_sub * Decimal(str(line.tax_percentage)) / 100)
            subtotal += line_sub
            tax += line_tax
            lines.append({
                "item_id": item.id, "item_name": item.name, "unit": item.unit,
                "quantity": qty, "unit_price": float(price),
                "tax_percentage": float(line.tax_percentage), "line_subtotal": float(line_sub),
                "line_tax": float(line_tax), "line_total": float(line_sub + line_tax),
            })  # fmt: skip
        total = subtotal + tax
        approval = permissions.requires_approval(user.role, float(total))
        tranches = tranches_from_terms(vendor.payment_terms)
        requests = _requests_covered(conn, lines)
        payload = {
            "vendor_id": vendor.id, "vendor_name": vendor.name,
            "payment_terms": vendor.payment_terms, "lines": lines,
            "subtotal": float(subtotal), "tax": float(tax), "total": float(total),
            "expected_delivery_date": expected.isoformat() if expected else None,
            "location_id": loc.id if loc else None, "location_name": loc.name if loc else None,
            "status": "pending_approval" if approval else "placed",
            "tranches": tranches, "request_ids": [r["id"] for r in requests],
        }  # fmt: skip
        fields: list[tuple[str, str]] = [("Vendor", vendor.name)]
        for i, ln in enumerate(lines, 1):
            qty_text = fmt_qty(float(ln["quantity"]), str(ln["unit"]))
            each, line_total = inr(float(ln["unit_price"])), inr(float(ln["line_total"]))
            fields.append((f"Line {i}", f"{ln['item_name']}: {qty_text} × {each} = {line_total}"))
        fields += [
            ("Subtotal", inr(float(subtotal))),
            ("Tax", inr(float(tax))),
            ("Total", inr(float(total))),
            ("Expected delivery", _day(expected)),
            ("Payment", _terms_text(tranches)),
        ]
        if loc:
            fields.append(("Deliver to", loc.name))
        effects = ["A new PO number is given when you confirm."]
        if approval:
            limit = inr(get_settings().po_approval_threshold_inr)
            effects.append(
                f"Over {limit}, so it waits for the Owner's approval before it is placed."
            )
        effects += [f"Covers the open stock request for {fmt_qty(r['quantity'], r['unit'])} of "
                    f"{r['item_name']} from {r['requested_by']}." for r in requests]  # fmt: skip
        summary = (f"Place a purchase order with {vendor.name} for {len(lines)} "
                   f"line{'s' if len(lines) > 1 else ''}, total {inr(float(total))}.")  # fmt: skip
        return Plan(payload=payload, summary=summary, fields=fields, effects=effects)

    def read_state(
        self, conn: psycopg.Connection, payload: dict[str, Any], lock: bool
    ) -> dict[str, Any]:
        sql = REQUESTS_LOCKED if lock else REQUESTS
        ids = payload["request_ids"]
        rows = conn.execute(sql, (ids,)).fetchall() if ids else []
        return {"requests": {r[0]: [r[1], r[2]] for r in rows}}

    def apply(
        self, conn: psycopg.Connection, user: AuthUser, payload: dict[str, Any],
        state: dict[str, Any],
    ) -> Outcome:  # fmt: skip
        row = conn.execute("SELECT nextval('po_number_seq')").fetchone()
        assert row is not None
        po_number = f"PO-{app_today().year}-{int(row[0]):04d}"
        po_id, stamp, status = str(uuid.uuid4()), business_now(), payload["status"]
        expected = (
            [payload["expected_delivery_date"]] if payload["expected_delivery_date"] else None
        )
        conn.execute(
            """INSERT INTO proc_purchase_orders (id, po_number, vendor_id, status, placed_on,
                   currency, subtotal, tax_amount, total_amount, payment_terms,
                   delivery_location_id, created_by, created_at, updated_at,
                   expected_delivery_date)
               VALUES (%s, %s, %s, %s, %s, 'INR', %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (po_id, po_number, payload["vendor_id"], status, app_today(), payload["subtotal"],
             payload["tax"], payload["total"], payload["payment_terms"], payload["location_id"],
             str(user.id), stamp, stamp, Json(expected) if expected else None),
        )  # fmt: skip
        for position, ln in enumerate(payload["lines"]):
            conn.execute(
                """INSERT INTO proc_po_lines (id, po_id, inv_item_id, quantity_ordered,
                       quantity_received, unit, unit_price, tax_percentage, line_subtotal,
                       line_tax, line_total, position, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (str(uuid.uuid4()), po_id, ln["item_id"], ln["quantity"], ln["unit"],
                 ln["unit_price"], ln["tax_percentage"], ln["line_subtotal"], ln["line_tax"],
                 ln["line_total"], position, stamp, stamp),
            )  # fmt: skip
        total = Decimal(str(payload["total"]))
        for sequence, t in enumerate(payload["tranches"]):
            conn.execute(
                """INSERT INTO proc_po_payment_tranches (id, po_id, sequence, stage, percentage,
                       amount, is_advance, blocks_dispatch, due_basis, due_offset_days, source,
                       created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'app', %s, %s)""",
                (str(uuid.uuid4()), po_id, sequence, t["stage"], t["percentage"],
                 _money(total * t["percentage"] / 100), t["is_advance"], t["blocks_dispatch"],
                 t["due_basis"], t["due_offset_days"], stamp, stamp),
            )  # fmt: skip
        events: list[dict[str, Any]] = []
        if status == "pending_approval":
            conn.execute(
                """INSERT INTO approvals (id, entity_type, entity_id, requested_by)
                   VALUES (%s, 'purchase_order', %s, %s)""",
                (uuid.uuid4(), po_id, user.id),
            )
            events.append({"event": "approval_requested", "data": {
                "po_number": po_number, "vendor_name": payload["vendor_name"],
                "total_amount": payload["total"], "requested_by_name": user.full_name,
            }})  # fmt: skip
        if payload["request_ids"]:
            conn.execute(
                """UPDATE stock_requests SET po_id = %s, handled_by = %s, updated_at = now(),
                       status = CASE WHEN %s = 'placed' THEN 'ordered' ELSE status END
                   WHERE id = ANY(%s::uuid[])""",
                (po_id, user.id, status, payload["request_ids"]),
            )
            if status == "placed":
                events += _ordered_events(conn, po_number, payload)
        total_text = inr(payload["total"])
        if status == "pending_approval":
            message = (
                f"Done: {po_number} for {total_text} with {payload['vendor_name']} is "
                "waiting for the Owner's approval. It will be placed once approved."
            )
        else:
            message = f"Done: {po_number} for {total_text} placed with {payload['vendor_name']}."
        return Outcome(
            message=message, entity_id=po_id,
            after={"po_number": po_number, "status": status, "total": payload["total"]},
            events=events,
            status="awaiting_approval" if status == "pending_approval" else "success",
        )  # fmt: skip


def _requests_covered(
    conn: psycopg.Connection, lines: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Open stock requests this PO covers: same item, quantity within the ordered quantity."""
    found = []
    for ln in lines:
        for rid, qty, who in conn.execute(
            """SELECT r.id::text, r.quantity::float, u.full_name FROM stock_requests r
               JOIN users u ON u.id = r.requested_by
               WHERE r.item_id = %s AND r.status = 'open' AND r.po_id IS NULL
                 AND r.quantity <= %s ORDER BY r.created_at""",
            (ln["item_id"], ln["quantity"]),
        ).fetchall():
            found.append({"id": rid, "quantity": qty, "item_name": ln["item_name"],
                          "unit": ln["unit"], "requested_by": who})  # fmt: skip
    return found


def _ordered_events(
    conn: psycopg.Connection, po_number: str, payload: dict[str, Any]
) -> list[dict[str, Any]]:
    by_item = {ln["item_id"]: ln for ln in payload["lines"]}
    events = []
    for item_id, qty, requester in conn.execute(
        "SELECT item_id, quantity::float, requested_by::text FROM stock_requests "
        "WHERE id = ANY(%s::uuid[])",
        (payload["request_ids"],),
    ).fetchall():
        ln = by_item[item_id]
        events.append({"event": "stock_request_ordered", "data": {
            "item_name": ln["item_name"], "quantity": qty, "unit": ln["unit"],
            "po_number": po_number, "expected_delivery_date": payload["expected_delivery_date"],
            "vendor_name": payload["vendor_name"], "unit_price": ln["unit_price"],
            "total_amount": payload["total"],
        }, "to": {"users": [requester]}})  # fmt: skip
    return events


# ------------------------------------------------------------------ receive_against_po
class ReceiveArgs(_Args):
    po: str = Field(min_length=1, max_length=40)
    item: str | None = Field(default=None, max_length=200)
    quantity: float
    location: str = Field(min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=300)


PO_ROW = "SELECT status FROM proc_purchase_orders WHERE id = %s"
PO_LOCKED = PO_ROW + " FOR UPDATE"
LINE_ROW = "SELECT quantity_received::float FROM proc_po_lines WHERE id = %s"
LINE_LOCKED = LINE_ROW + " FOR UPDATE"


class ReceiveAgainstPO(Action):
    name = "receive_against_po"
    entity_type = "purchase_order"
    args_model = ReceiveArgs

    def plan(self, conn: psycopg.Connection, user: AuthUser, args: ReceiveArgs) -> Plan:
        po = resolve.purchase_order(conn, user.role, args.po)
        if po.status not in OPEN_FOR_RECEIPT:
            raise WriteError(f"{po.number} isn't open for receiving.")
        loc = resolve.location(conn, user.role, args.location)
        quantity = check_quantity(args.quantity)
        open_lines = conn.execute(
            """SELECT l.id, l.inv_item_id, i.name, coalesce(i.unit, l.unit),
                      l.quantity_ordered::float, l.quantity_received::float
               FROM proc_po_lines l JOIN inv_items i ON i.id = l.inv_item_id
               WHERE l.po_id = %s AND l.quantity_received < l.quantity_ordered
               ORDER BY l.position, l.id""",
            (po.id,),
        ).fetchall()
        if not open_lines:
            raise WriteError(f"Everything on {po.number} has already been received.")
        if args.item:
            item = resolve.item(conn, user.role, args.item)
            chosen = [ln for ln in open_lines if ln[1] == item.id]
            if not chosen:
                raise WriteError(f"{item.name} isn't waiting to be received on {po.number}.")
        elif len(open_lines) == 1:
            chosen = open_lines
        else:
            raise Ambiguous(f"{po.number} has several items waiting. Which one arrived?",
                            [ln[2] for ln in open_lines], field="item")  # fmt: skip
        line_id, item_id, item_name, unit, ordered, received = chosen[0]
        pending = ordered - received
        if quantity > pending:
            raise WriteError(
                f"Only {fmt_qty(pending, unit)} of {item_name} is still due on {po.number}. "
                "Record anything extra as an unplanned delivery."
            )
        here = stock_rows(conn, item_id, [loc.id], lock=False)[loc.id] or 0.0
        payload = {
            "po_id": po.id, "po_number": po.number, "line_id": line_id, "item_id": item_id,
            "item_name": item_name, "unit": unit, "quantity": quantity, "ordered": ordered,
            "location_id": loc.id, "location_name": loc.name, "note": args.note,
        }  # fmt: skip
        return Plan(
            payload=payload,
            summary=f"Receive {fmt_qty(quantity, unit)} of {item_name} against {po.number} "
            f"into {loc.name}.",
            fields=[("PO", po.number), ("Item", item_name), ("Quantity", fmt_qty(quantity, unit)),
                    ("Warehouse", loc.name)] + ([("Note", args.note)] if args.note else []),
            effects=[f"{po.number}: {fmt_qty(received, unit)} → "
                     f"{fmt_qty(received + quantity, unit)} of {fmt_qty(ordered, unit)} received",
                     f"{loc.name}: {fmt_qty(here, unit)} → {fmt_qty(here + quantity, unit)}"],
        )  # fmt: skip

    def read_state(
        self, conn: psycopg.Connection, payload: dict[str, Any], lock: bool
    ) -> dict[str, Any]:
        po_sql, line_sql = (PO_LOCKED, LINE_LOCKED) if lock else (PO_ROW, LINE_ROW)
        po = conn.execute(po_sql, (payload["po_id"],)).fetchone()
        line = conn.execute(line_sql, (payload["line_id"],)).fetchone()
        stock = stock_rows(conn, payload["item_id"], [payload["location_id"]], lock)
        return {"po": po[0] if po else None, "received": line[0] if line else None,
                "stock": stock}  # fmt: skip

    def apply(
        self, conn: psycopg.Connection, user: AuthUser, payload: dict[str, Any],
        state: dict[str, Any],
    ) -> Outcome:  # fmt: skip
        q = float(payload["quantity"])
        price = conn.execute("SELECT unit_price FROM proc_po_lines WHERE id = %s",
                             (payload["line_id"],)).fetchone()  # fmt: skip
        tx_id, now_here = change_stock(
            conn, user, item_id=payload["item_id"], location_id=payload["location_id"],
            transaction_type="inbound", quantity=q, reference_type="purchase_order",
            reference_id=payload["po_id"], notes=payload["note"],
            unit_cost=float(price[0]) if price and price[0] is not None else None,
        )  # fmt: skip
        conn.execute(
            """INSERT INTO proc_po_receipts (id, po_line_id, quantity, location_id, notes,
                   inv_transaction_id, received_by, received_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (str(uuid.uuid4()), payload["line_id"], q, payload["location_id"], payload["note"],
             tx_id, str(user.id), business_now()),
        )  # fmt: skip
        row = conn.execute(
            """UPDATE proc_po_lines SET quantity_received = quantity_received + %s,
                   updated_at = localtimestamp WHERE id = %s RETURNING quantity_received::float""",
            (q, payload["line_id"]),
        ).fetchone()
        assert row is not None
        done = conn.execute(
            "SELECT bool_and(quantity_received >= quantity_ordered) FROM proc_po_lines "
            "WHERE po_id = %s",
            (payload["po_id"],),
        ).fetchone()
        status = "received" if done and done[0] else "partial"
        conn.execute("UPDATE proc_purchase_orders SET status = %s, updated_at = localtimestamp "
                     "WHERE id = %s", (status, payload["po_id"]))  # fmt: skip
        if status == "received":
            conn.execute(
                "UPDATE stock_requests SET status = 'fulfilled', updated_at = now() "
                "WHERE po_id = %s AND status = 'ordered'",
                (payload["po_id"],),
            )
        unit, po_number = payload["unit"], payload["po_number"]
        so_far, ordered = fmt_qty(row[0], unit), fmt_qty(payload["ordered"], unit)
        message = (
            f"Done: {fmt_qty(q, unit)} of {payload['item_name']} received against {po_number} "
            f"into {payload['location_name']}. {so_far} of {ordered} received so far."
        )
        event = {"event": "po_receipt", "data": {
            "po_number": po_number, "item_name": payload["item_name"], "quantity": q,
            "received_total": row[0], "ordered": payload["ordered"], "unit": unit,
            "status": status,
        }}  # fmt: skip
        return Outcome(message=message, entity_id=payload["po_id"],
                       after={"status": status, "received": row[0],
                              "stock": {payload["location_id"]: now_here}},
                       events=[event])  # fmt: skip


# ------------------------------------------------------------------ record_unexpected_receipt
class UnexpectedArgs(_Args):
    item: str = Field(min_length=1, max_length=200)
    quantity: float
    location: str = Field(min_length=1, max_length=80)
    vendor_name: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=300)


class RecordUnexpectedReceipt(Action):
    name = "record_unexpected_receipt"
    entity_type = "unexpected_receipt"
    args_model = UnexpectedArgs

    def plan(self, conn: psycopg.Connection, user: AuthUser, args: UnexpectedArgs) -> Plan:
        item = resolve.item(conn, user.role, args.item)
        loc = resolve.location(conn, user.role, args.location)
        quantity = check_quantity(args.quantity)
        here = stock_rows(conn, item.id, [loc.id], lock=False)[loc.id] or 0.0
        payload = {
            "item_id": item.id, "item_name": item.name, "unit": item.unit,
            "quantity": quantity, "location_id": loc.id, "location_name": loc.name,
            "vendor_name": args.vendor_name, "note": args.note,
        }  # fmt: skip
        fields = [
            ("Item", f"{item.name} ({item.code})"),
            ("Quantity", fmt_qty(quantity, item.unit)),
            ("Warehouse", loc.name),
        ]
        if args.vendor_name:
            fields.append(("Delivered by", args.vendor_name))
        if args.note:
            fields.append(("Note", args.note))
        return Plan(
            payload=payload,
            summary=f"Record an unplanned delivery of {fmt_qty(quantity, item.unit)} of "
            f"{item.name} into {loc.name}.",
            fields=fields,
            effects=[
                f"{loc.name}: {fmt_qty(here, item.unit)} → {fmt_qty(here + quantity, item.unit)}",
                "Procurement is told, so they can match it to a PO.",
            ],
        )  # fmt: skip

    def read_state(
        self, conn: psycopg.Connection, payload: dict[str, Any], lock: bool
    ) -> dict[str, Any]:
        return {"stock": stock_rows(conn, payload["item_id"], [payload["location_id"]], lock)}

    def apply(
        self, conn: psycopg.Connection, user: AuthUser, payload: dict[str, Any],
        state: dict[str, Any],
    ) -> Outcome:  # fmt: skip
        header_id, receipt_id, stamp = str(uuid.uuid4()), str(uuid.uuid4()), business_now()
        conn.execute(
            """INSERT INTO proc_unexpected_receipt_headers (id, received_by, received_at, notes,
                   vendor_name) VALUES (%s, %s, %s, %s, %s)""",
            (header_id, str(user.id), stamp, payload["note"], payload["vendor_name"]),
        )
        tx_id, now_here = change_stock(
            conn, user, item_id=payload["item_id"], location_id=payload["location_id"],
            transaction_type="inbound", quantity=float(payload["quantity"]),
            reference_type="unexpected_receipt", reference_id=header_id, notes=payload["note"],
        )  # fmt: skip
        conn.execute(
            """INSERT INTO proc_unexpected_receipts (id, inv_item_id, quantity, location_id,
                   notes, inv_transaction_id, received_by, received_at, header_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (receipt_id, payload["item_id"], payload["quantity"], payload["location_id"],
             payload["note"], tx_id, str(user.id), stamp, header_id),
        )  # fmt: skip
        q, unit = float(payload["quantity"]), payload["unit"]
        return Outcome(
            message=f"Done: unplanned delivery of {fmt_qty(q, unit)} of {payload['item_name']} "
            f"recorded in {payload['location_name']}, which now has {fmt_qty(now_here, unit)}.",
            entity_id=receipt_id, after={"stock": {payload["location_id"]: now_here}},
            events=[{"event": "unexpected_receipt", "data": {
                "item_name": payload["item_name"], "quantity": q, "unit": unit,
                "location_name": payload["location_name"], "vendor_name": payload["vendor_name"],
            }}],
        )  # fmt: skip


# ------------------------------------------------------------------ PO changes
PO_STATE = """SELECT status, expected_delivery_date::text,
                     (SELECT coalesce(sum(quantity_received), 0)::float FROM proc_po_lines
                      WHERE po_id = p.id)
              FROM proc_purchase_orders p WHERE id = %s"""
PO_STATE_LOCKED = PO_STATE + " FOR UPDATE OF p"


class POStateMixin:
    def read_state(
        self, conn: psycopg.Connection, payload: dict[str, Any], lock: bool
    ) -> dict[str, Any]:
        row = conn.execute(PO_STATE_LOCKED if lock else PO_STATE, (payload["po_id"],)).fetchone()
        return {"po": list(row) if row else None}


def _linked_request_items(
    conn: psycopg.Connection, po_id: str
) -> list[tuple[str, float, str, str]]:
    return conn.execute(
        """SELECT i.name, r.quantity::float, coalesce(i.unit, 'pcs'), r.requested_by::text
           FROM stock_requests r
           JOIN inv_items i ON i.id = r.item_id WHERE r.po_id = %s""",
        (po_id,),
    ).fetchall()


class UpdateDateArgs(_Args):
    po: str = Field(min_length=1, max_length=40)
    expected_delivery_date: date


class UpdatePOExpectedDate(POStateMixin, Action):
    name = "update_po_expected_date"
    entity_type = "purchase_order"
    args_model = UpdateDateArgs

    def plan(self, conn: psycopg.Connection, user: AuthUser, args: UpdateDateArgs) -> Plan:
        po = resolve.purchase_order(conn, user.role, args.po)
        if po.status not in OPEN_FOR_CHANGE:
            raise WriteError(
                f"{po.number} is {po.status.replace('_', ' ')}, so its date can't change."
            )
        new = _future(args.expected_delivery_date, "new expected delivery date")
        row = conn.execute(
            """SELECT (SELECT max(d::date) FROM json_array_elements_text(
                         CASE WHEN json_typeof(expected_delivery_date) = 'array'
                              THEN expected_delivery_date END) d)
               FROM proc_purchase_orders WHERE id = %s""",
            (po.id,),
        ).fetchone()
        old = row[0] if row else None
        payload = {
            "po_id": po.id,
            "po_number": po.number,
            "expected_delivery_date": new.isoformat(),
        }
        return Plan(
            payload=payload,
            summary=f"Change the expected delivery of {po.number} to {_day(new)}.",
            fields=[("PO", po.number), ("Expected delivery", f"{_day(old)} → {_day(new)}")],
            effects=["Every open line of the PO gets the new date."],
        )

    def apply(
        self, conn: psycopg.Connection, user: AuthUser, payload: dict[str, Any],
        state: dict[str, Any],
    ) -> Outcome:  # fmt: skip
        new = Json([payload["expected_delivery_date"]])
        conn.execute(
            "UPDATE proc_purchase_orders SET expected_delivery_date = %s, "
            "updated_at = localtimestamp WHERE id = %s",
            (new, payload["po_id"]),
        )
        conn.execute(
            "UPDATE proc_po_lines SET expected_delivery_date = %s, updated_at = localtimestamp "
            "WHERE po_id = %s AND quantity_received < quantity_ordered",
            (new, payload["po_id"]),
        )
        events = [{"event": "po_date_changed", "data": {
            "po_number": payload["po_number"], "item_name": name, "quantity": qty, "unit": unit,
            "expected_delivery_date": payload["expected_delivery_date"],
        }, "to": {"users": [requester]}}
            for name, qty, unit, requester in _linked_request_items(
                conn, payload["po_id"])]  # fmt: skip
        return Outcome(
            message=f"Done: {payload['po_number']} is now expected on "
            f"{_day(payload['expected_delivery_date'])}.",
            entity_id=payload["po_id"],
            after={"expected_delivery_date": payload["expected_delivery_date"]}, events=events,
        )  # fmt: skip


class CancelArgs(_Args):
    po: str = Field(min_length=1, max_length=40)
    reason: str | None = Field(default=None, max_length=300)


class CancelPO(POStateMixin, Action):
    name = "cancel_po"
    entity_type = "purchase_order"
    args_model = CancelArgs

    def plan(self, conn: psycopg.Connection, user: AuthUser, args: CancelArgs) -> Plan:
        po = resolve.purchase_order(conn, user.role, args.po)
        if po.status not in ("pending_approval", "placed"):
            raise WriteError(
                f"{po.number} is {po.status.replace('_', ' ')}; only a PO with nothing received "
                "can be cancelled."
            )
        received = conn.execute(
            "SELECT coalesce(sum(quantity_received), 0) FROM proc_po_lines WHERE po_id = %s",
            (po.id,),
        ).fetchone()
        if received and received[0] > 0:
            raise WriteError(f"Part of {po.number} has been received, so it can't be cancelled.")
        linked = _linked_request_items(conn, po.id)
        effects = ["The PO stays on record with the status cancelled."]
        effects += [f"The stock request for {fmt_qty(q, u)} of {n} goes back to open."
                    for n, q, u, _ in linked]  # fmt: skip
        fields = [("PO", po.number), ("Total", inr(po.total))]
        if args.reason:
            fields.append(("Reason", args.reason))
        return Plan(payload={"po_id": po.id, "po_number": po.number, "reason": args.reason},
                    summary=f"Cancel {po.number}.", fields=fields, effects=effects)  # fmt: skip

    def apply(
        self, conn: psycopg.Connection, user: AuthUser, payload: dict[str, Any],
        state: dict[str, Any],
    ) -> Outcome:  # fmt: skip
        conn.execute("UPDATE proc_purchase_orders SET status = 'cancelled', "
                     "updated_at = localtimestamp WHERE id = %s", (payload["po_id"],))  # fmt: skip
        conn.execute(
            """UPDATE approvals SET status = 'rejected', decided_by = %s, decided_at = now(),
                   note = 'PO cancelled before a decision'
               WHERE entity_type = 'purchase_order' AND entity_id = %s AND status = 'pending'""",
            (user.id, payload["po_id"]),
        )
        conn.execute(
            "UPDATE stock_requests SET status = 'open', po_id = NULL, updated_at = now() "
            "WHERE po_id = %s AND status IN ('open', 'ordered')",
            (payload["po_id"],),
        )
        return Outcome(message=f"Done: {payload['po_number']} is cancelled.",
                       entity_id=payload["po_id"],
                       after={"status": "cancelled", "reason": payload["reason"]})  # fmt: skip


# ------------------------------------------------------------------ clear_payment_tranche
class ClearTrancheArgs(_Args):
    po: str = Field(min_length=1, max_length=40)
    stage: str | None = Field(default=None, max_length=80)


class ClearPaymentTranche(Action):
    name = "clear_payment_tranche"
    entity_type = "payment_tranche"
    args_model = ClearTrancheArgs

    def plan(self, conn: psycopg.Connection, user: AuthUser, args: ClearTrancheArgs) -> Plan:
        po = resolve.purchase_order(conn, user.role, args.po)
        rows = conn.execute(
            """SELECT t.id, t.stage,
                      coalesce(t.amount, round(p.total_amount * t.percentage / 100, 2))
               FROM proc_po_payment_tranches t JOIN proc_purchase_orders p ON p.id = t.po_id
               WHERE t.po_id = %s AND t.cleared_at IS NULL ORDER BY t.sequence""",
            (po.id,),
        ).fetchall()
        if args.stage:
            wanted = args.stage.lower()
            rows = [r for r in rows if wanted in (r[1] or "").lower()]
        if not rows:
            raise WriteError(f"{po.number} has no unpaid instalment"
                             + (f" matching “{args.stage}”." if args.stage else "."))  # fmt: skip
        if len(rows) > 1:
            raise Ambiguous(f"{po.number} has several unpaid instalments. Which one was paid?",
                            [f"{r[1]} ({inr(float(r[2]))})" for r in rows], field="stage",
                            values=[str(r[1]) for r in rows])  # fmt: skip
        tranche_id, stage, amount = rows[0]
        return Plan(
            payload={"tranche_id": tranche_id, "po_number": po.number, "stage": stage,
                     "amount": float(amount)},
            summary=f"Mark the {stage} instalment of {po.number} ({inr(float(amount))}) as paid.",
            fields=[
                ("PO", po.number),
                ("Instalment", stage or "—"),
                ("Amount", inr(float(amount))),
            ],
            effects=["Recorded as paid today, by you."],
        )  # fmt: skip

    def read_state(
        self, conn: psycopg.Connection, payload: dict[str, Any], lock: bool
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT cleared_at::text FROM proc_po_payment_tranches WHERE id = %s"  # noqa: S608
            + (" FOR UPDATE" if lock else ""),  # constant SQL; the id is a parameter
            (payload["tranche_id"],),
        ).fetchone()
        return {"cleared_at": row[0] if row else "missing"}

    def apply(
        self, conn: psycopg.Connection, user: AuthUser, payload: dict[str, Any],
        state: dict[str, Any],
    ) -> Outcome:  # fmt: skip
        conn.execute(
            """UPDATE proc_po_payment_tranches SET cleared_at = %s, cleared_by = %s,
                   updated_at = localtimestamp WHERE id = %s""",
            (business_now(), str(user.id), payload["tranche_id"]),
        )
        return Outcome(
            message=f"Done: the {payload['stage']} instalment of {payload['po_number']} "
            f"({inr(payload['amount'])}) is marked as paid.",
            entity_id=payload["tranche_id"], after={"cleared": True},
        )  # fmt: skip


# ------------------------------------------------------------------ set_reorder_policy
class ReorderArgs(_Args):
    item: str = Field(min_length=1, max_length=200)
    min_level: float
    reorder_qty: float
    lead_time_days: int
    safety_stock: float | None = None
    preferred_vendor: str | None = Field(default=None, max_length=120)


SETTINGS = ("min_level", "reorder_qty", "lead_time_days", "safety_stock")


class SetReorderPolicy(Action):
    name = "set_reorder_policy"
    entity_type = "reorder_policy"
    args_model = ReorderArgs

    def plan(self, conn: psycopg.Connection, user: AuthUser, args: ReorderArgs) -> Plan:
        item = resolve.item(conn, user.role, args.item)
        if args.min_level < 0 or (args.safety_stock or 0) < 0:
            raise WriteError("The reorder level and safety stock can't be negative.")
        reorder_qty = check_quantity(args.reorder_qty)
        if not 1 <= args.lead_time_days <= 365:
            raise WriteError("The lead time must be between 1 and 365 days.")
        vendor = resolve.vendor(conn, user.role, args.preferred_vendor, "preferred_vendor") \
            if args.preferred_vendor else None  # fmt: skip
        old = conn.execute(
            "SELECT min_level::float, reorder_qty::float, lead_time_days, safety_stock::float "
            "FROM reorder_policy WHERE item_id = %s",
            (item.id,),
        ).fetchone()
        safety = args.safety_stock if args.safety_stock is not None else (old[3] if old else 0.0)
        new = (float(args.min_level), reorder_qty, args.lead_time_days, float(safety))
        u = item.unit
        shown = [
            ("Reorder level", fmt_qty(new[0], u), fmt_qty(old[0], u) if old else None),
            ("Reorder quantity", fmt_qty(new[1], u), fmt_qty(old[1], u) if old else None),
            ("Lead time", f"{new[2]} days", f"{old[2]} days" if old else None),
            ("Safety stock", fmt_qty(new[3], u), fmt_qty(old[3], u) if old else None),
        ]
        fields = [("Item", f"{item.name} ({item.code})")] + [
            (label, f"{before} → {after}" if before and before != after else after)
            for label, after, before in shown
        ]
        if vendor:
            fields.append(("Preferred vendor", vendor.name))
        payload = {
            "item_id": item.id, "item_name": item.name, "min_level": new[0], "reorder_qty": new[1],
            "lead_time_days": new[2], "safety_stock": new[3],
            "vendor_id": vendor.id if vendor else None,
        }  # fmt: skip
        verb = "Update" if old else "Set"
        return Plan(payload=payload, summary=f"{verb} the reorder settings for {item.name}.",
                    fields=fields,
                    effects=["Reorder alerts use the new settings from now on."])  # fmt: skip

    def read_state(
        self, conn: psycopg.Connection, payload: dict[str, Any], lock: bool
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT updated_at::text FROM reorder_policy WHERE item_id = %s"  # noqa: S608
            + (" FOR UPDATE" if lock else ""),  # constant SQL; the id is a parameter
            (payload["item_id"],),
        ).fetchone()
        return {"updated_at": row[0] if row else None}

    def apply(
        self, conn: psycopg.Connection, user: AuthUser, payload: dict[str, Any],
        state: dict[str, Any],
    ) -> Outcome:  # fmt: skip
        conn.execute(
            """INSERT INTO reorder_policy (id, item_id, min_level, reorder_qty, lead_time_days,
                   safety_stock, preferred_vendor_id, source, updated_by, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'app', %s, now())
               ON CONFLICT (item_id) DO UPDATE SET min_level = EXCLUDED.min_level,
                   reorder_qty = EXCLUDED.reorder_qty, lead_time_days = EXCLUDED.lead_time_days,
                   safety_stock = EXCLUDED.safety_stock,
                   preferred_vendor_id = coalesce(EXCLUDED.preferred_vendor_id,
                                                  reorder_policy.preferred_vendor_id),
                   source = 'app', updated_by = EXCLUDED.updated_by, updated_at = now()""",
            (uuid.uuid4(), payload["item_id"], payload["min_level"], payload["reorder_qty"],
             payload["lead_time_days"], payload["safety_stock"], payload["vendor_id"], user.id),
        )  # fmt: skip
        return Outcome(message=f"Done: reorder settings saved for {payload['item_name']}.",
                       entity_id=payload["item_id"],
                       after={k: payload[k] for k in SETTINGS})  # fmt: skip


# ------------------------------------------------------------------ create_vendor
class VendorArgs(_Args):
    name: str = Field(min_length=2, max_length=120)
    city: str | None = Field(default=None, max_length=80)
    payment_terms: str | None = Field(default=None, max_length=120)


class CreateVendor(Action):
    name = "create_vendor"
    entity_type = "vendor"
    args_model = VendorArgs

    def plan(self, conn: psycopg.Connection, user: AuthUser, args: VendorArgs) -> Plan:
        similar = conn.execute(
            "SELECT name FROM proc_vendors WHERE lower(name) = lower(%s) "
            "OR similarity(lower(name), lower(%s)) >= 0.8 ORDER BY name LIMIT 3",
            (args.name, args.name),
        ).fetchall()
        if similar:
            raise WriteError(f"A vendor with a very similar name already exists: "
                             f"{', '.join(r[0] for r in similar)}.")  # fmt: skip
        fields = [("Name", args.name)]
        if args.city:
            fields.append(("City", args.city))
        if args.payment_terms:
            fields.append(("Payment terms", args.payment_terms))
            fields.append(("Instalments", _terms_text(tranches_from_terms(args.payment_terms))))
        return Plan(payload={"name": args.name, "city": args.city,
                             "payment_terms": args.payment_terms},
                    summary=f"Add the vendor {args.name}.", fields=fields,
                    effects=["The vendor can be used on new purchase orders."])  # fmt: skip

    def read_state(
        self, conn: psycopg.Connection, payload: dict[str, Any], lock: bool
    ) -> dict[str, Any]:
        row = conn.execute("SELECT count(*) FROM proc_vendors WHERE lower(name) = lower(%s)",
                           (payload["name"],)).fetchone()  # fmt: skip
        return {"same_name": row[0] if row else 0}

    def apply(
        self, conn: psycopg.Connection, user: AuthUser, payload: dict[str, Any],
        state: dict[str, Any],
    ) -> Outcome:  # fmt: skip
        vendor_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO proc_vendors (id, name, city, payment_terms, is_active, created_by,
                   created_at, updated_at)
               VALUES (%s, %s, %s, %s, true, %s, localtimestamp, localtimestamp)""",
            (vendor_id, payload["name"], payload["city"], payload["payment_terms"], str(user.id)),
        )
        return Outcome(message=f"Done: {payload['name']} added as a vendor.", entity_id=vendor_id,
                       after={"name": payload["name"]})  # fmt: skip


for _action in (CreatePurchaseOrder(), ReceiveAgainstPO(), RecordUnexpectedReceipt(),
                UpdatePOExpectedDate(), CancelPO(), ClearPaymentTranche(), SetReorderPolicy(),
                CreateVendor()):  # fmt: skip
    register(_action)
