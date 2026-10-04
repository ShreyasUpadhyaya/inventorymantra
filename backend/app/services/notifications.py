"""Role-scoped notifications (C6.5; CLAUDE.md §5, WRITE_PATH.md §4).

A write produces *events* with full detail. Each recipient gets a notification rendered for
their role from an allow-list of fields: the title, body and payload are built only from
those fields, so an Inventory Manager's notification can't carry a vendor, price or the
Owner's note even if the event has them. Events a role must not receive are refused.

Notifications are written in the same transaction as the change that caused them, one row
per recipient (so "read" is per person). The reorder scan adds a daily "reorder now" for
the buyers, de-duplicated per item, person and day.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from app.agent.formatting import inr, number, show

IM, PM, OW = "inventory_manager", "procurement_manager", "owner"
Data = dict[str, Any]


@dataclass(frozen=True)
class Notification:
    title: str
    body: str
    payload: Data


@dataclass(frozen=True)
class EventSpec:
    fields: dict[str, tuple[str, ...]]  # role -> fields that role may see
    title: str
    body: Callable[[Data], str]
    default_roles: tuple[str, ...] = ()  # who gets it when the event names no one


def _qty(d: Data) -> str:
    quantity = d.get("quantity")
    if quantity is None:
        return ""
    return f"{number(float(quantity))} {d.get('unit') or 'pcs'}".strip()


def _day(value: Any) -> str:
    return show("date", str(value)) if value else "not recorded"


def _money(value: Any) -> str:
    return inr(float(value)) if value is not None else ""


def _ordered_body(d: Data) -> str:
    when = _day(d.get("expected_delivery_date"))
    text = f"{_qty(d)} of {d['item_name']} is on order ({d['po_number']}), expected {when}."
    if "vendor_name" in d:
        text += f" Vendor: {d['vendor_name']}, PO total {_money(d.get('total_amount'))}."
    return text


def _decided_body(d: Data) -> str:
    approved = d["status"] == "approved"
    if "item_name" in d and "total_amount" not in d:  # the requester of a linked stock request
        if approved:
            return (f"The order for {d['item_name']} ({d['po_number']}) was approved; expected "
                    f"{_day(d.get('expected_delivery_date'))}.")  # fmt: skip
        return f"The order for {d['item_name']} was not approved; your request is open again."
    text = f"{d['po_number']} was {'approved and placed' if approved else 'rejected'}."
    if d.get("note"):
        text += f" Note: {d['note']}"
    return text


def _low_body(d: Data) -> str:
    text = f"{d['item_name']} at {d['location_name']} is down to {number(float(d['quantity']))}."
    if "reorder_point" in d:
        text += f" Reorder level: {number(float(d['reorder_point']))}."
    if d.get("preferred_vendor"):
        text += f" Usual vendor: {d['preferred_vendor']}."
    return text


def _reorder_body(d: Data) -> str:
    on_hand = f"{number(float(d['on_hand']))} {d.get('unit') or ''}".strip()
    order = f"{number(float(d['reorder_qty']))} by {_day(d.get('order_by_date'))}"
    text = f"{d['item_name']} ({d['item_code']}): {on_hand} on hand; order {order}."
    if d.get("preferred_vendor"):
        text += f" Usual vendor: {d['preferred_vendor']}."
    return text


ORDERED_IM = ("item_name", "quantity", "unit", "po_number", "expected_delivery_date")
DECIDED_IM = ("po_number", "status", "item_name", "quantity", "unit", "expected_delivery_date")
DECIDED_PM = ("po_number", "status", "note", "vendor_name", "total_amount",
              "expected_delivery_date")  # fmt: skip
LOW_IM = ("item_name", "location_name", "quantity")
LOW_BUYER = LOW_IM + ("reorder_point", "order_by_date", "preferred_vendor", "last_unit_price")
REORDER = ("item_code", "item_name", "on_hand", "unit", "reorder_qty", "order_by_date",
           "preferred_vendor")  # fmt: skip
RECEIPT = ("po_number", "item_name", "quantity", "unit", "received_total", "ordered", "status")
UNPLANNED = ("item_name", "quantity", "unit", "location_name", "vendor_name")
DATE_CHANGE = ("po_number", "item_name", "quantity", "unit", "expected_delivery_date")
RAISED = ("item_name", "quantity", "unit", "needed_by", "requested_by_name")

EVENTS: dict[str, EventSpec] = {
    "stock_request_raised": EventSpec(
        {PM: RAISED, OW: RAISED},
        "New stock request",
        lambda d: (
            f"{d.get('requested_by_name') or 'Inventory'} asks for {_qty(d)} of "
            f"{d['item_name']}"
            + (f", needed by {_day(d['needed_by'])}." if d.get("needed_by") else ".")
        ),
        (PM, OW),
    ),
    "stock_request_ordered": EventSpec(
        {
            IM: ORDERED_IM,
            PM: ORDERED_IM + ("vendor_name", "total_amount"),
            OW: ORDERED_IM + ("vendor_name", "total_amount"),
        },
        "Your stock request is on order",
        _ordered_body,
    ),  # fmt: skip
    "approval_requested": EventSpec(
        {OW: ("po_number", "vendor_name", "total_amount", "requested_by_name")},
        "Purchase order waiting for your approval",
        lambda d: (
            f"{d['po_number']} for {_money(d['total_amount'])} with "
            f"{d.get('vendor_name') or 'a vendor'}, "
            f"from {d.get('requested_by_name') or 'procurement'}."
        ),
        (OW,),
    ),
    "approval_decided": EventSpec(
        {IM: DECIDED_IM, PM: DECIDED_PM, OW: DECIDED_PM + ("decided_by_name",)},
        "Purchase order decision",
        _decided_body,
    ),
    "approval_question": EventSpec(
        {PM: ("po_number", "question"), OW: ("po_number", "question")},
        "The Owner asks about your purchase order",
        lambda d: f"About {d['po_number']}: {d['question']}",
    ),
    "approval_reply": EventSpec(
        {OW: ("po_number", "reply", "replied_by_name")},
        "Reply about a purchase order",
        lambda d: f"{d.get('replied_by_name') or 'Procurement'} on {d['po_number']}: {d['reply']}",
        (OW,),
    ),
    "low_stock": EventSpec(
        {IM: LOW_IM, PM: LOW_BUYER, OW: LOW_BUYER}, "Low stock", _low_body, (PM, OW)
    ),  # fmt: skip
    "reorder_now": EventSpec({PM: REORDER, OW: REORDER}, "Reorder now", _reorder_body, (PM,)),
    "po_receipt": EventSpec(
        {PM: RECEIPT, OW: RECEIPT},
        "Goods received against a PO",
        lambda d: (
            f"{_qty(d)} of {d['item_name']} received on {d['po_number']} "
            f"({number(float(d['received_total']))} of {number(float(d['ordered']))} so far)."
        ),
        (PM,),
    ),
    "unexpected_receipt": EventSpec(
        {PM: UNPLANNED, OW: UNPLANNED},
        "Unplanned delivery",
        lambda d: (
            f"{_qty(d)} of {d['item_name']} arrived at {d['location_name']} with no PO"
            + (f" (from {d['vendor_name']})." if d.get("vendor_name") else ".")
        ),
        (PM,),
    ),
    "po_date_changed": EventSpec(
        {IM: DATE_CHANGE, PM: DATE_CHANGE, OW: DATE_CHANGE},
        "Delivery date changed",
        lambda d: (
            f"{d['item_name']} on {d['po_number']} is now expected "
            f"{_day(d['expected_delivery_date'])}."
        ),
    ),
}


def render(event: str, data: Data, recipient_role: str) -> Notification:
    """The notification `recipient_role` may see. ValueError for an unknown event,
    PermissionError for a role that must not receive it."""
    spec = EVENTS.get(event)
    if spec is None:
        raise ValueError(f"unknown event {event!r}")
    allowed = spec.fields.get(recipient_role)
    if allowed is None:
        raise PermissionError(f"{recipient_role} does not receive {event}")
    payload = {k: data[k] for k in allowed if k in data and data[k] is not None}
    body = spec.body(payload).replace("..", ".")  # "Pvt. Ltd.." -> "Pvt. Ltd."
    return Notification(title=spec.title, body=body, payload=payload)


def _recipients(conn: psycopg.Connection, event: Data) -> list[tuple[UUID, str]]:
    spec = EVENTS[event["event"]]
    target = event.get("to") or {}
    if target.get("users"):
        rows = conn.execute(
            "SELECT id, role FROM users WHERE is_active AND id = ANY(%s::uuid[])",
            ([str(u) for u in target["users"]],),
        ).fetchall()
    else:
        roles = list(target.get("roles") or spec.default_roles)
        rows = conn.execute(
            "SELECT id, role FROM users WHERE is_active AND role = ANY(%s)", (roles,)
        ).fetchall() if roles else []  # fmt: skip
    return [(r[0], r[1]) for r in rows]


def emit(conn: psycopg.Connection, events: list[Data], actor_id: UUID | None = None) -> int:
    """Write one notification per recipient (never to the person who made the change).
    Returns how many were written; a repeat with the same dedupe key is skipped."""
    written = 0
    for event in events:
        for user_id, role in _recipients(conn, event):
            if actor_id is not None and user_id == actor_id:
                continue
            try:
                note = render(event["event"], event["data"], role)
            except PermissionError:
                continue  # e.g. an event addressed to someone whose role may not see it
            dedupe = f"{event['dedupe']}:{user_id}" if event.get("dedupe") else None
            cur = conn.execute(
                """INSERT INTO notifications (id, recipient_user_id, recipient_role, type, title,
                       body, payload, dedupe_key)
                   VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (dedupe_key) DO NOTHING""",
                (user_id, role, event["event"], note.title, note.body, Jsonb(note.payload),
                 dedupe),
            )  # fmt: skip
            written += cur.rowcount
    return written


def reorder_events(rows: list[tuple[Any, ...]], today: date) -> list[Data]:
    """reorder_status rows (item_id, item_code, item_name, on_hand, unit, reorder_qty,
    order_by_date, preferred_vendor) -> one de-duplicated event per item per day."""
    events = []
    for item_id, code, name, on_hand, unit, qty, order_by, vendor in rows:
        events.append({"event": "reorder_now", "dedupe": f"reorder:{item_id}:{today}", "data": {
            "item_code": code, "item_name": name, "on_hand": float(on_hand), "unit": unit,
            "reorder_qty": float(qty), "order_by_date": str(order_by) if order_by else None,
            "preferred_vendor": vendor,
        }})  # fmt: skip
    return events


def scan_reorder(today: date) -> int:
    """The daily lead-time scan: items to reorder now -> notifications for the buyers.
    Reads through the Procurement Manager's view (same formula as the dashboard, D-011)."""
    from app.db.pool import get_pool
    from app.db.readonly import readonly_session

    with readonly_session("procurement_manager", None, today) as ro:
        rows = ro.execute(
            """SELECT item_id, item_code, item_name, on_hand, unit, reorder_qty, order_by_date,
                      preferred_vendor
               FROM reorder_status WHERE status = 'reorder_now' ORDER BY order_by_date"""
        ).fetchall()
    with get_pool().connection() as conn:
        return emit(conn, reorder_events(rows, today))


if __name__ == "__main__":  # python -m app.services.notifications  (make scan)
    from app.core.config import app_today

    print(f"reorder scan: {scan_reorder(app_today())} new notifications")
