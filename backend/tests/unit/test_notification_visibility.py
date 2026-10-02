"""Notification contract: payloads carry only what the recipient's role may see
(CLAUDE.md §5; WRITE_PATH.md §4). Implementation: app/services/notifications.py in C6.5.

render(event, data, recipient_role) -> object with `title: str`, `body: str`,
`payload: dict`. It builds each notification per recipient role from an allow-list of
fields, never by forwarding the raw event data.
"""

import json
from typing import Any

import pytest

from tests.pending import pending

pytestmark = pending("C6.5")

IM, PM, OW = "inventory_manager", "procurement_manager", "owner"

# Realistic event data, deliberately including sensitive fields.
EVENTS: dict[str, dict[str, Any]] = {
    "stock_request_raised": {
        "item_name": "Hex Nut – SS 304 – M3", "quantity": 200, "unit": "pcs",
        "needed_by": "2026-10-10", "requested_by_name": "Demo Inventory Manager",
    },
    "stock_request_ordered": {
        "item_name": "Hex Nut – SS 304 – M3", "quantity": 500, "unit": "pcs",
        "po_number": "PO-2026-0058", "expected_delivery_date": "2026-10-12",
        "vendor_name": "Shakti Steels Pvt. Ltd.", "unit_price": 2.5, "total_amount": 1250,
    },
    "approval_requested": {
        "po_number": "PO-2026-0059", "vendor_name": "Sterling Steels Pvt. Ltd.",
        "total_amount": 270000, "requested_by_name": "Demo Procurement Manager",
    },
    "approval_decided": {
        "po_number": "PO-2026-0059", "status": "approved", "note": "OK, negotiate freight",
        "decided_by_name": "Demo Owner", "vendor_name": "Sterling Steels Pvt. Ltd.",
        "total_amount": 270000, "item_name": "SS Sheet – 304 – 1500×3000×2mm",
        "expected_delivery_date": "2026-10-20",
    },
    "low_stock": {
        "item_name": "Relay Base RXZE1M2C", "location_name": "132-1", "quantity": 0,
        "reorder_point": 46.6, "order_by_date": "2026-08-17",
        "preferred_vendor": "Vision Intech Solution", "last_unit_price": 310.0,
    },
}  # fmt: skip

# Which roles receive which events (others must be refused).
RECIPIENTS = {
    "stock_request_raised": {PM, OW},
    "stock_request_ordered": {IM, PM, OW},
    "approval_requested": {OW},
    "approval_decided": {IM, PM, OW},
    "low_stock": {IM, PM, OW},
}

IM_FORBIDDEN_KEYS = {
    "vendor_name", "preferred_vendor", "unit_price", "last_unit_price", "total_amount",
    "decided_by_name", "note",
}  # fmt: skip
IM_FORBIDDEN_VALUES = [
    "Shakti Steels", "Sterling Steels", "Vision Intech", "1250", "1,250", "270000",
    "2,70,000", "2.5", "310", "negotiate", "Demo Owner",
]  # fmt: skip


def render(event: str, role: str) -> Any:
    from app.services.notifications import render as _render

    return _render(event, EVENTS[event], recipient_role=role)


def everything(n: Any) -> str:
    return " ".join([n.title, n.body, json.dumps(n.payload, ensure_ascii=False, default=str)])


@pytest.mark.parametrize(
    "event", [e for e, roles in RECIPIENTS.items() if IM in roles]
)  # fmt: skip
def test_inventory_manager_never_sees_vendor_price_or_owner_data(event: str) -> None:
    n = render(event, IM)
    assert set(n.payload).isdisjoint(IM_FORBIDDEN_KEYS)
    text = everything(n)
    assert [v for v in IM_FORBIDDEN_VALUES if v in text] == []


def test_inventory_manager_learns_what_they_need() -> None:
    n = render("stock_request_ordered", IM)
    assert {"item_name", "quantity", "expected_delivery_date"} <= set(n.payload)
    assert "2026-10-12" in everything(n) or "12 Oct 2026" in everything(n)


def test_procurement_manager_gets_the_decision_but_not_owner_internals() -> None:
    n = render("approval_decided", PM)
    assert n.payload["status"] == "approved"
    assert n.payload["po_number"] == "PO-2026-0059"
    assert "decided_by_name" not in n.payload


def test_owner_sees_full_approval_request() -> None:
    n = render("approval_requested", OW)
    assert {"po_number", "vendor_name", "total_amount"} <= set(n.payload)


@pytest.mark.parametrize(
    ("event", "role"),
    [(e, r) for e, roles in RECIPIENTS.items() for r in (IM, PM, OW) if r not in roles],
)
def test_events_are_refused_for_roles_that_must_not_receive_them(event: str, role: str) -> None:
    with pytest.raises(PermissionError):
        render(event, role)


def test_unknown_event_is_rejected() -> None:
    from app.services.notifications import render as _render

    with pytest.raises(ValueError):
        _render("drop_everything", {}, recipient_role=OW)


def test_payload_is_built_from_an_allow_list_not_forwarded() -> None:
    data = dict(EVENTS["low_stock"], internal_field="should never travel")
    from app.services.notifications import render as _render

    for role in (IM, PM, OW):
        n = _render("low_stock", data, recipient_role=role)
        assert "internal_field" not in n.payload
        assert "should never travel" not in everything(n)
