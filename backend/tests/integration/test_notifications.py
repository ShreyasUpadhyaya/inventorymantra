"""Notifications from real writes (C6.5): the right people, role-scoped content, written in
the same transaction, de-duplicated scans, and an API that shows only your own."""

# ruff: noqa: F811  (fixtures imported from test_users are used as parameters)

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import app_today
from app.services.notifications import emit, reorder_events
from tests.api.test_users import (  # noqa: F401
    DOMAIN,
    _requires_jwt_secret,
    make_user,
    session,
)
from tests.integration.conftest import World
from tests.integration.test_procurement_writes import number_of, place, run

IM, PM, OW = "inventory_manager", "procurement_manager", "owner"
IM_NEVER = ["Zeta Test Supplies", "₹", "2500", "2,500", "Demo Owner", "Too expensive"]


def inbox(world: World, role: str, kind: str | None = None) -> list[dict[str, Any]]:
    rows = world.db.execute(
        """SELECT type, title, body, payload::text, recipient_role FROM notifications
           WHERE recipient_user_id = %s AND payload::text LIKE '%%Zeta%%'
             AND (%s::text IS NULL OR type = %s) ORDER BY created_at""",
        (world.users[role].id, kind, kind),
    ).fetchall()
    return [{"type": t, "title": ti, "body": b, "payload": json.loads(p), "role": r}
            for t, ti, b, p, r in rows]  # fmt: skip


def text(notes: list[dict[str, Any]]) -> str:
    return " ".join(f"{n['title']} {n['body']} {json.dumps(n['payload'])}" for n in notes)


def test_a_stock_request_reaches_the_buyers_not_the_requester(world: World) -> None:
    run(world, "raise_stock_request", IM, item="TS-ZW-0002", quantity=200)
    [pm] = inbox(world, PM, "stock_request_raised")
    assert pm["body"] == "Demo Inventory Manager asks for 200 pcs of Zeta Test Widget Beta."
    assert len(inbox(world, OW, "stock_request_raised")) == 1
    assert inbox(world, IM) == []  # the person who made the change isn't notified


def test_ordered_request_tells_the_requester_without_vendor_or_price(world: World) -> None:
    run(world, "raise_stock_request", IM, item="TS-ZW-0001", quantity=40)
    place(world, role=OW)
    [note] = inbox(world, IM, "stock_request_ordered")
    assert note["role"] == IM and set(note["payload"]) == {
        "item_name", "quantity", "unit", "po_number", "expected_delivery_date"}  # fmt: skip
    assert [w for w in IM_NEVER if w in text([note])] == []


def test_approval_round_trip(world: World) -> None:
    run(world, "raise_stock_request", IM, item="TS-ZW-0001", quantity=40)
    _, po = place(world, role=PM, price=2500)
    number = number_of(world, po.entity_id)
    [asked] = inbox(world, OW, "approval_requested")
    assert asked["payload"]["total_amount"] == 148000 and asked["payload"]["po_number"] == number
    run(world, "decide_approval", OW, po=number, decision="reject", note="Too expensive")
    [pm] = inbox(world, PM, "approval_decided")
    assert pm["payload"]["status"] == "rejected" and "decided_by_name" not in pm["payload"]
    assert "Too expensive" in pm["body"]  # the note is for the requester
    [im] = inbox(world, IM, "approval_decided")
    assert (
        im["body"]
        == "The order for Zeta Test Widget Alpha was not approved; your request is open again."
    )
    assert [w for w in IM_NEVER if w in text([im])] == []


def test_low_stock_goes_to_the_buyers(world: World) -> None:
    world.db.execute(
        """INSERT INTO reorder_policy (id, item_id, min_level, reorder_qty, lead_time_days)
           VALUES (%s, %s, 5, 20, 10)""",
        (uuid.uuid4(), world.alpha),
    )
    run(world, "record_stock_movement", IM, kind="outbound", item="TS-ZW-0001", location="132-1",
        quantity=6)  # fmt: skip
    [pm] = inbox(world, PM, "low_stock")
    assert pm["body"] == "Zeta Test Widget Alpha at 132-1 is down to 4. Reorder level: 5."
    assert len(inbox(world, OW, "low_stock")) == 1 and inbox(world, IM) == []


def test_a_failed_write_sends_nothing(world: World) -> None:
    from app.services.writes import WriteError, apply, preview

    p = preview(world.users[IM], "raise_stock_request", {"item": "TS-ZW-0002", "quantity": 5})
    with pytest.raises(WriteError):
        apply(world.users[IM], p.model_copy(update={"preview_hash": "0" * 64}), "0" * 64,
              f"test-{uuid.uuid4()}")  # fmt: skip
    assert inbox(world, PM) == []


def test_reorder_scan_is_once_per_item_per_day(world: World) -> None:
    rows = [(world.alpha, "TS-ZW-0001", "Zeta Test Widget Alpha", 0, "pcs", 20, app_today(),
             "Zeta Test Supplies")]  # fmt: skip
    first = emit(world.db, reorder_events(rows, app_today()))
    again = emit(world.db, reorder_events(rows, app_today()))
    assert first >= 1 and again == 0
    [note] = inbox(world, PM, "reorder_now")
    assert note["body"].startswith(
        "Zeta Test Widget Alpha (TS-ZW-0001): 0 pcs on hand; order 20 by"
    )
    assert inbox(world, IM, "reorder_now") == []  # the buyers' alert, not the IM's


def test_api_shows_only_your_own(world: World, make_user: Any) -> None:
    mine_id, other_id = make_user(IM, "zim"), make_user(IM, "zother")
    for user_id in (mine_id, other_id):
        emit(world.db, [{"event": "po_date_changed", "to": {"users": [str(user_id)]}, "data": {
            "po_number": "PO-2026-0003", "item_name": "Zeta Test Widget Alpha", "quantity": 5,
            "unit": "pcs", "expected_delivery_date": "2026-10-20"}}])  # fmt: skip
    client: TestClient = session("zim")
    listed = client.get("/notifications").json()
    assert listed["unread"] == 1 and len(listed["items"]) == 1
    [item] = listed["items"]
    assert item["body"] == "Zeta Test Widget Alpha on PO-2026-0003 is now expected 20 Oct 2026."
    others = world.db.execute("SELECT id FROM notifications WHERE recipient_user_id = %s",
                              (other_id,)).fetchone()  # fmt: skip
    assert client.post(f"/notifications/{others[0]}/read").status_code == 404
    assert client.post(f"/notifications/{item['id']}/read").status_code == 204
    assert client.get("/notifications").json()["unread"] == 0
    assert client.post("/notifications/read-all").status_code == 204
    assert TestClient(client.app).get("/notifications").status_code == 401
