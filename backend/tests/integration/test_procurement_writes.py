"""Procurement services (WRITE_PATH.md §4): POs, receipts, unplanned deliveries, dates,
cancellation, payment instalments, reorder settings and vendors."""

import json
import re
import uuid
from datetime import timedelta
from typing import Any

import pytest

from app.core.config import app_today
from app.services.writes import WriteError, apply, preview
from app.services.writes.framework import Ambiguous, NotPermitted
from app.services.writes.procurement import tranches_from_terms
from tests.integration.conftest import World

IM, PM, OW = "inventory_manager", "procurement_manager", "owner"
SOON = (app_today() + timedelta(days=14)).isoformat()


def run(world: World, action: str, role: str, **args: Any) -> tuple[Any, Any]:
    p = preview(world.users[role], action, args)
    return p, apply(world.users[role], p, p.preview_hash, f"test-{uuid.uuid4()}")


def place(world: World, role: str = OW, price: float = 100.0, **extra: Any) -> tuple[Any, Any]:
    """A two-line PO with the test vendor: 50 Alpha and 20 Beta."""
    args = {"vendor": "Zeta Test Supplies", "expected_delivery_date": SOON,
            "lines": [{"item": "TS-ZW-0001", "quantity": 50, "unit_price": price,
                       "tax_percentage": 18},
                      {"item": "TS-ZW-0002", "quantity": 20, "unit_price": 25}]}  # fmt: skip
    return run(world, "create_purchase_order", role, **{**args, **extra})


def po_row(world: World, po_id: str) -> tuple:
    return world.db.execute(
        """SELECT po_number, status, subtotal::float, tax_amount::float, total_amount::float,
                  expected_delivery_date::text, created_by
           FROM proc_purchase_orders WHERE id = %s""",
        (po_id,),
    ).fetchone()


def number_of(world: World, po_id: str) -> str:
    return po_row(world, po_id)[0]


# ------------------------------------------------------------------ payment terms
@pytest.mark.parametrize(
    ("terms", "expected"),
    [
        ("50% Advance & 50% Against Dispatch", [("Advance", 50), ("Against dispatch", 50)]),
        ("30% Adv, Bal Against Dispatch", [("Advance", 30), ("Against dispatch", 70)]),
        ("20% Adv & Balance 45 Days Credit", [("Advance", 20), ("45 days credit", 80)]),
        ("50% Advance and Balance 30 Days Credit", [("Advance", 50), ("30 days credit", 50)]),
        ("100% Payment Adavcne", [("Advance", 100)]),
        ("100% Advance", [("Advance", 100)]),
        ("30 Days Credit", [("30 days credit", 100)]),
        ("10 Days PDC Against Dispatch", [("10 days PDC against dispatch", 100)]),
        ("10 Days PDC", [("10 days PDC against dispatch", 100)]),
        ("100% Payment Against Dispatch", [("Against dispatch", 100)]),
        (None, [("As per terms", 100)]),
        ("as discussed on call", [("As per terms", 100)]),
    ],
)
def test_instalments_follow_the_vendor_terms(terms: str | None, expected: list) -> None:
    tranches = tranches_from_terms(terms)
    assert [(t["stage"], t["percentage"]) for t in tranches] == expected
    assert sum(t["percentage"] for t in tranches) == 100


# ------------------------------------------------------------------ create PO
def test_owner_places_a_po_with_server_computed_totals(world: World) -> None:
    p, result = place(world)
    assert p.summary == ("Place a purchase order with Zeta Test Supplies for 2 lines, "
                         "total ₹6,400.")  # fmt: skip
    assert ("Line 1", "Zeta Test Widget Alpha: 50 pcs × ₹100 = ₹5,900") in [
        (f.label, f.value) for f in p.fields]  # fmt: skip
    assert ("Payment", "30% advance, 70% against dispatch") in [
        (f.label, f.value) for f in p.fields
    ]
    number, status, subtotal, tax, total, expected, created_by = po_row(world, result.entity_id)
    assert re.fullmatch(rf"PO-{app_today().year}-\d{{4}}", number) and int(number[-4:]) >= 58
    assert (status, subtotal, tax, total) == ("placed", 5500.0, 900.0, 6400.0)
    assert json.loads(expected) == [SOON] and created_by == str(world.users[OW].id)
    tranches = world.db.execute(
        "SELECT stage, percentage::float, amount::float, source FROM proc_po_payment_tranches "
        "WHERE po_id = %s ORDER BY sequence",
        (result.entity_id,),
    ).fetchall()
    assert tranches == [("Advance", 30.0, 1920.0, "app"), ("Against dispatch", 70.0, 4480.0, "app")]
    assert (
        result.status == "success"
        and result.message == f"Done: {number} for ₹6,400 placed with Zeta Test Supplies."
    )


def test_po_numbers_follow_on(world: World) -> None:
    _, a = place(world)
    _, b = place(world)
    assert int(number_of(world, b.entity_id)[-4:]) == int(number_of(world, a.entity_id)[-4:]) + 1


def test_a_po_covers_an_open_stock_request_and_tells_the_requester(world: World) -> None:
    _, req = run(world, "raise_stock_request", IM, item="TS-ZW-0001", quantity=40)
    p, result = place(world)
    assert any("Covers the open stock request for 40 pcs" in e for e in p.effects)
    status, po_id = world.db.execute(
        "SELECT status, po_id FROM stock_requests WHERE id = %s", (req.entity_id,)
    ).fetchone()
    assert (status, po_id) == ("ordered", result.entity_id)
    [ordered] = [e for e in result.events if e["event"] == "stock_request_ordered"]
    assert ordered["data"]["expected_delivery_date"] == SOON


def test_pm_order_over_the_limit_waits_for_the_owner(world: World) -> None:
    _, req = run(world, "raise_stock_request", IM, item="TS-ZW-0001", quantity=40)
    p, result = place(world, role=PM, price=2500)  # 50 × 2,500 + 18% = ₹1,47,500 + ₹500
    assert any("waits for the Owner's approval" in e for e in p.effects)
    assert (
        result.status == "awaiting_approval"
        and "waiting for the Owner's approval" in result.message
    )
    assert po_row(world, result.entity_id)[1] == "pending_approval"
    approval = world.db.execute(
        "SELECT status, requested_by FROM approvals WHERE entity_id = %s", (result.entity_id,)
    ).fetchone()
    assert approval == ("pending", world.users[PM].id)
    assert [e["event"] for e in result.events] == ["approval_requested"]
    status, po_id = world.db.execute(
        "SELECT status, po_id FROM stock_requests WHERE id = %s", (req.entity_id,)
    ).fetchone()
    assert (status, po_id) == ("open", result.entity_id)  # ordered only once it's placed
    audit = world.db.execute(
        "SELECT status FROM agent_audit_log WHERE operation_id = %s", (result.operation_id,)
    ).fetchone()
    assert audit == ("awaiting_approval",)


def test_pm_order_under_the_limit_is_placed(world: World) -> None:
    _, result = place(world, role=PM)
    assert result.status == "success" and po_row(world, result.entity_id)[1] == "placed"


def test_owner_orders_never_need_approval(world: World) -> None:
    _, result = place(world, role=OW, price=2500)
    assert po_row(world, result.entity_id)[1] == "placed"


def test_inventory_manager_cannot_place_orders(world: World) -> None:
    with pytest.raises(NotPermitted):
        preview(world.users[IM], "create_purchase_order", {"vendor": "x", "lines": []})


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"lines": [{"item": "TS-ZW-0001", "quantity": 1, "unit_price": 1},
                    {"item": "TS-ZW-0001", "quantity": 2, "unit_price": 1}]}, "on two lines"),
        ({"lines": [{"item": "TS-ZW-0001", "quantity": 1, "unit_price": 0}]}, "unit price"),
        ({"lines": [{"item": "TS-ZW-0001", "quantity": 1, "unit_price": 1,
                     "tax_percentage": 40}]}, "between 0% and 28%"),
        ({"lines": [{"item": "TS-ZW-0001", "quantity": 1, "unit_price": 1}] * 21}, "at most 20"),
        ({"expected_delivery_date": (app_today() - timedelta(days=1)).isoformat()}, "in the past"),
        ({"vendor": "Nonexistent Traders"}, "couldn't find"),
        ({"lines": []}, "missing or invalid: lines"),
    ],
)  # fmt: skip
def test_bad_orders_are_refused(world: World, change: dict[str, Any], message: str) -> None:
    base = {"vendor": "Zeta Test Supplies",
            "lines": [{"item": "TS-ZW-0001", "quantity": 1, "unit_price": 1}]}  # fmt: skip
    with pytest.raises(WriteError, match=message):
        preview(world.users[PM], "create_purchase_order", {**base, **change})


# ------------------------------------------------------------------ receive against a PO
def test_inventory_manager_receives_without_seeing_price_or_vendor(world: World) -> None:
    _, po = place(world)
    number = number_of(world, po.entity_id)
    p, result = run(world, "receive_against_po", IM, po=number, item="TS-ZW-0001", quantity=30,
                    location="132-2")  # fmt: skip
    shown = " ".join([p.summary, *p.effects, *(f"{f.label} {f.value}" for f in p.fields)])
    assert "₹" not in shown and "Zeta Test Supplies" not in shown and "100" not in shown
    assert p.effects[0] == f"{number}: 0 pcs → 30 pcs of 50 pcs received"
    assert result.message.endswith("30 pcs of 50 pcs received so far.")
    tx = world.db.execute(
        """SELECT t.transaction_type, t.quantity, t.reference_type, t.reference_id, t.unit_cost
           FROM proc_po_receipts r JOIN inv_transactions t ON t.id = r.inv_transaction_id
           WHERE t.item_id = %s""",
        (world.alpha,),
    ).fetchone()
    assert tx == ("inbound", 30.0, "purchase_order", po.entity_id, 100.0)  # cost set by server
    assert world.stock(world.alpha, "132-2") == 30
    assert po_row(world, po.entity_id)[1] == "partial"


def test_receiving_everything_closes_the_po_and_fulfils_the_request(world: World) -> None:
    _, req = run(world, "raise_stock_request", IM, item="TS-ZW-0002", quantity=20)
    _, po = place(world)
    number = number_of(world, po.entity_id)
    run(
        world, "receive_against_po", IM, po=number, item="TS-ZW-0001", quantity=50, location="132-1"
    )
    _, last = run(world, "receive_against_po", PM, po=number, quantity=20, location="132-1")
    assert po_row(world, po.entity_id)[1] == "received"
    assert last.events[0]["data"]["status"] == "received"
    status = world.db.execute("SELECT status FROM stock_requests WHERE id = %s",
                              (req.entity_id,)).fetchone()  # fmt: skip
    assert status == ("fulfilled",)
    with pytest.raises(WriteError, match="isn't open for receiving"):
        preview(world.users[IM], "receive_against_po",
                {"po": number, "quantity": 1, "location": "132-1"})  # fmt: skip


def test_receipt_checks(world: World) -> None:
    _, po = place(world)
    number = number_of(world, po.entity_id)
    base = {"po": number, "quantity": 1, "location": "132-1"}
    with pytest.raises(Ambiguous) as which:
        preview(world.users[IM], "receive_against_po", base)  # two items still due
    assert set(which.value.options) == {"Zeta Test Widget Alpha", "Zeta Test Widget Beta"}
    with pytest.raises(WriteError, match="Only 50 pcs .* still due"):
        preview(
            world.users[IM], "receive_against_po", {**base, "item": "TS-ZW-0001", "quantity": 51}
        )
    with pytest.raises(WriteError, match="isn't waiting to be received"):
        preview(world.users[IM], "receive_against_po", {**base, "item": "BO-EL-0140"})


def test_a_po_awaiting_approval_cant_be_received(world: World) -> None:
    _, po = place(world, role=PM, price=2500)
    with pytest.raises(WriteError, match="isn't open for receiving"):
        preview(world.users[IM], "receive_against_po",
                {"po": number_of(world, po.entity_id), "item": "TS-ZW-0001", "quantity": 1,
                 "location": "132-1"})  # fmt: skip


# ------------------------------------------------------------------ unplanned delivery
def test_unplanned_delivery_is_recorded_and_linked(world: World) -> None:
    p, result = run(world, "record_unexpected_receipt", IM, item="TS-ZW-0002", quantity=12,
                    location="132-3", vendor_name="Some Courier", note="invoice 991")  # fmt: skip
    assert (
        p.summary == "Record an unplanned delivery of 12 pcs of Zeta Test Widget Beta into 132-3."
    )
    row = world.db.execute(
        """SELECT h.vendor_name, h.notes, r.quantity::float, t.reference_type, t.reference_id = h.id
           FROM proc_unexpected_receipts r
           JOIN proc_unexpected_receipt_headers h ON h.id = r.header_id
           JOIN inv_transactions t ON t.id = r.inv_transaction_id WHERE r.id = %s""",
        (result.entity_id,),
    ).fetchone()
    assert row == ("Some Courier", "invoice 991", 12.0, "unexpected_receipt", True)
    assert world.stock(world.beta, "132-3") == 12
    assert result.events[0]["event"] == "unexpected_receipt"


# ------------------------------------------------------------------ date, cancel
def test_new_expected_date_updates_po_and_open_lines(world: World) -> None:
    _, req = run(world, "raise_stock_request", IM, item="TS-ZW-0001", quantity=10)
    _, po = place(world)
    number, later = number_of(world, po.entity_id), (app_today() + timedelta(days=30)).isoformat()
    p, result = run(world, "update_po_expected_date", PM, po=number, expected_delivery_date=later)
    assert p.fields[1].value.endswith(" → " + p.summary.rsplit(" to ", 1)[1].rstrip("."))
    assert json.loads(po_row(world, po.entity_id)[5]) == [later]
    lines = world.db.execute(
        "SELECT expected_delivery_date::text FROM proc_po_lines WHERE po_id = %s", (po.entity_id,)
    ).fetchall()
    assert {json.loads(r[0])[0] for r in lines} == {later}
    assert result.events[0]["event"] == "po_date_changed" and req.entity_id
    with pytest.raises(WriteError, match="in the past"):
        preview(world.users[PM], "update_po_expected_date",
                {"po": number, "expected_delivery_date": "2020-01-01"})  # fmt: skip


def test_cancel_reopens_the_request_and_closes_a_pending_approval(world: World) -> None:
    _, req = run(world, "raise_stock_request", IM, item="TS-ZW-0001", quantity=10)
    _, po = place(world, role=PM, price=2500)
    number = number_of(world, po.entity_id)
    p, _ = run(world, "cancel_po", PM, po=number, reason="vendor can't supply")
    assert any("goes back to open" in e for e in p.effects)
    assert po_row(world, po.entity_id)[1] == "cancelled"
    assert world.db.execute("SELECT status FROM approvals WHERE entity_id = %s",
                            (po.entity_id,)).fetchone() == ("rejected",)  # fmt: skip
    assert world.db.execute("SELECT status, po_id FROM stock_requests WHERE id = %s",
                            (req.entity_id,)).fetchone() == ("open", None)  # fmt: skip
    with pytest.raises(WriteError, match="can't change"):
        preview(world.users[PM], "update_po_expected_date",
                {"po": number, "expected_delivery_date": SOON})  # fmt: skip


def test_a_partly_received_po_cant_be_cancelled(world: World) -> None:
    _, po = place(world)
    number = number_of(world, po.entity_id)
    run(world, "receive_against_po", IM, po=number, item="TS-ZW-0001", quantity=1, location="132-1")
    with pytest.raises(WriteError, match="only a PO with nothing received"):
        preview(world.users[OW], "cancel_po", {"po": number})


# ------------------------------------------------------------------ payment instalments
def test_clearing_an_instalment(world: World) -> None:
    _, po = place(world)
    number = number_of(world, po.entity_id)
    with pytest.raises(Ambiguous) as which:
        preview(world.users[PM], "clear_payment_tranche", {"po": number})
    assert which.value.options == ["Advance (₹1,920)", "Against dispatch (₹4,480)"]
    p, result = run(world, "clear_payment_tranche", PM, po=number, stage="advance")
    assert p.summary == f"Mark the Advance instalment of {number} (₹1,920) as paid."
    cleared = world.db.execute(
        "SELECT cleared_by FROM proc_po_payment_tranches WHERE id = %s", (result.entity_id,)
    ).fetchone()
    assert cleared == (str(world.users[PM].id),)
    with pytest.raises(WriteError, match="no unpaid instalment matching"):
        preview(world.users[PM], "clear_payment_tranche", {"po": number, "stage": "advance"})
    with pytest.raises(NotPermitted):
        preview(world.users[IM], "clear_payment_tranche", {"po": number})


# ------------------------------------------------------------------ reorder settings, vendors
def test_reorder_settings_show_old_and_new_values(world: World) -> None:
    run(world, "set_reorder_policy", PM, item="TS-ZW-0001", min_level=5, reorder_qty=20,
        lead_time_days=10)  # fmt: skip
    p, _ = run(world, "set_reorder_policy", OW, item="TS-ZW-0001", min_level=8, reorder_qty=20,
               lead_time_days=12, preferred_vendor="Zeta Test Supplies")  # fmt: skip
    values = {f.label: f.value for f in p.fields}
    assert values["Reorder level"] == "5 pcs → 8 pcs" and values["Reorder quantity"] == "20 pcs"
    assert values["Lead time"] == "10 days → 12 days"
    row = world.db.execute(
        "SELECT min_level::float, lead_time_days, preferred_vendor_id, source FROM reorder_policy "
        "WHERE item_id = %s",
        (world.alpha,),
    ).fetchone()
    assert row == (8.0, 12, world.vendor, "app")
    with pytest.raises(WriteError, match="between 1 and 365"):
        preview(
            world.users[PM],
            "set_reorder_policy",
            {"item": "TS-ZW-0001", "min_level": 1, "reorder_qty": 1, "lead_time_days": 0},
        )
    with pytest.raises(NotPermitted):
        preview(
            world.users[IM],
            "set_reorder_policy",
            {"item": "TS-ZW-0001", "min_level": 1, "reorder_qty": 1, "lead_time_days": 5},
        )


def test_new_vendor_and_no_near_duplicates(world: World) -> None:
    p, result = run(world, "create_vendor", PM, name="Zeta Test Fasteners", city="Rajkot",
                    payment_terms="50% Advance & 50% Against Dispatch")  # fmt: skip
    assert ("Instalments", "50% advance, 50% against dispatch") in [
        (f.label, f.value) for f in p.fields
    ]
    assert result.entity_id
    with pytest.raises(WriteError, match="very similar name already exists"):
        preview(world.users[PM], "create_vendor", {"name": "zeta test fasteners"})
    with pytest.raises(NotPermitted):
        preview(world.users[IM], "create_vendor", {"name": "Zeta Test Bolts"})
