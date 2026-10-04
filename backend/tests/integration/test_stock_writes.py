"""Stock movement, transfer and stock request services (WRITE_PATH.md §4)."""

import uuid
from datetime import timedelta
from typing import Any

import pytest

from app.core.config import app_today
from app.services.writes import WriteError, apply, preview
from app.services.writes.framework import Ambiguous
from tests.integration.conftest import World

IM, PM, OW = "inventory_manager", "procurement_manager", "owner"


def run(world: World, action: str, role: str = IM, **args: Any) -> Any:
    p = preview(world.users[role], action, args)
    return p, apply(world.users[role], p, p.preview_hash, f"test-{uuid.uuid4()}")


# ------------------------------------------------------------------ movements
def test_inbound_adds_a_signed_transaction_and_creates_the_stock_row(world: World) -> None:
    p, result = run(world, "record_stock_movement", kind="inbound", item="Zeta Test Widget Beta",
                    location="132-2 godown", quantity=3)  # fmt: skip
    assert p.summary == "Add 3 pcs of Zeta Test Widget Beta to 132-2."
    assert p.effects == ["132-2: 0 pcs → 3 pcs"]
    assert (
        result.message
        == "Done: 3 pcs of Zeta Test Widget Beta added to 132-2. 132-2 now has 3 pcs."
    )
    assert world.stock(world.beta, "132-2") == 3
    [(kind, qty, ref, _, loc, _, issued, cost, by)] = world.transactions(world.beta)
    assert (kind, qty, ref, loc, issued, cost) == (
        "inbound", 3.0, "manual_adjustment", world.loc["132-2"], None, None)  # fmt: skip
    assert by == str(world.users[IM].id)


def test_issue_goes_to_a_team_with_a_negative_quantity(world: World) -> None:
    p, _ = run(world, "record_stock_movement", kind="issue", item="TS-ZW-0001",
               location="132-1", quantity=2, issued_to="fm assembly")  # fmt: skip
    assert p.summary == "Issue 2 pcs of Zeta Test Widget Alpha from 132-1 to C84 FM assembly."
    [(kind, qty, ref, _, _, _, issued, _, _)] = world.transactions(world.alpha)
    assert (kind, qty, ref) == ("outbound", -2.0, "warehouse_issue") and issued is not None
    assert world.stock(world.alpha, "132-1") == 8


def test_issue_needs_a_team_and_only_issues_have_one(world: World) -> None:
    with pytest.raises(WriteError, match="who the stock is issued to"):
        preview(
            world.users[IM],
            "record_stock_movement",
            {"kind": "issue", "item": "TS-ZW-0001", "location": "132-1", "quantity": 1},
        )
    with pytest.raises(WriteError, match="Only an issue"):
        preview(world.users[IM], "record_stock_movement",
                {"kind": "outbound", "item": "TS-ZW-0001", "location": "132-1", "quantity": 1,
                 "issued_to": "Dispatch team"})  # fmt: skip
    with pytest.raises(Ambiguous) as many:
        preview(world.users[IM], "record_stock_movement",
                {"kind": "issue", "item": "TS-ZW-0001", "location": "132-1", "quantity": 1,
                 "issued_to": "C84"})  # fmt: skip
    assert len(many.value.options) == 3  # C84 Dispatch / Electronic assembly / FM assembly


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"quantity": 11}, "has only 10 pcs"),
        ({"quantity": 0}, "more than zero"),
        ({"quantity": -5}, "more than zero"),
        ({"quantity": 100_001}, "at most 100,000"),
        ({"item": "TS-ZW-0003"}, "inactive"),
        ({"location": "Unassigned"}, "couldn't find a warehouse"),
        ({"item": "flux capacitor"}, "couldn't find an item"),
    ],
)
def test_bad_movements_are_refused_before_any_preview(
    world: World, args: dict[str, Any], message: str
) -> None:
    base = {"kind": "outbound", "item": "TS-ZW-0001", "location": "132-1", "quantity": 1}
    with pytest.raises(WriteError, match=message):
        preview(world.users[IM], "record_stock_movement", {**base, **args})
    assert world.transactions(world.alpha) == []


def test_vague_item_asks_which_one(world: World) -> None:
    with pytest.raises(Ambiguous) as exc:
        preview(world.users[IM], "record_stock_movement",
                {"kind": "inbound", "item": "zeta test widget", "location": "132-1",
                 "quantity": 1})  # fmt: skip
    assert set(exc.value.options) >= {"Zeta Test Widget Alpha", "Zeta Test Widget Beta"}


def test_going_low_raises_a_low_stock_event_for_the_buyers(world: World) -> None:
    world.db.execute(
        """INSERT INTO reorder_policy (id, item_id, min_level, reorder_qty, lead_time_days)
           VALUES (%s, %s, 5, 20, 10)""",
        (uuid.uuid4(), world.alpha),
    )
    _, quiet = run(world, "record_stock_movement", kind="outbound", item="TS-ZW-0001",
                   location="132-1", quantity=4)  # fmt: skip
    assert quiet.events == []  # 6 left, above the level of 5
    _, low = run(world, "record_stock_movement", kind="outbound", item="TS-ZW-0001",
                 location="132-1", quantity=2)  # fmt: skip
    assert low.events == [{"event": "low_stock", "data": {
        "item_name": "Zeta Test Widget Alpha", "location_name": "132-1", "quantity": 4.0,
        "total_on_hand": 4.0, "reorder_point": 5.0}}]  # fmt: skip


# ------------------------------------------------------------------ transfers
def test_transfer_writes_a_linked_pair_and_both_stock_rows(world: World) -> None:
    p, result = run(world, "transfer_stock", item="TS-ZW-0001", from_location="132-1",
                    to_location="132-3", quantity=4)  # fmt: skip
    assert p.effects == ["132-1: 10 pcs → 6 pcs", "132-3: 0 pcs → 4 pcs"]
    assert result.message.startswith(
        "Done: moved 4 pcs of Zeta Test Widget Alpha from 132-1 to 132-3."
    )
    rows = {r[0]: r for r in world.transactions(world.alpha)}
    out, into = rows["transfer_out"], rows["transfer_in"]
    assert out[1] == -4 and into[1] == 4 and out[2] == into[2] == "transfer"
    assert out[5] == world.loc["132-3"] and into[5] == world.loc["132-1"]
    assert into[3] == result.entity_id  # transfer_in points at its transfer_out
    assert world.stock(world.alpha, "132-1") == 6 and world.stock(world.alpha, "132-3") == 4


@pytest.mark.parametrize(
    ("args", "message"),
    [({"to_location": "132-1"}, "the same"), ({"quantity": 50}, "has only 10 pcs")],
)
def test_bad_transfers_are_refused(world: World, args: dict[str, Any], message: str) -> None:
    base = {"item": "TS-ZW-0001", "from_location": "132-1", "to_location": "132-2", "quantity": 1}
    with pytest.raises(WriteError, match=message):
        preview(world.users[PM], "transfer_stock", {**base, **args})


# ------------------------------------------------------------------ stock requests
def test_stock_request_is_open_and_tells_procurement(world: World) -> None:
    needed = app_today() + timedelta(days=10)
    p, result = run(world, "raise_stock_request", item="TS-ZW-0002", quantity=200,
                    needed_by=needed.isoformat(), note="for the Delhi order")  # fmt: skip
    assert p.summary.startswith("Ask procurement for 200 pcs of Zeta Test Widget Beta, needed by ")
    row = world.db.execute(
        "SELECT status, quantity, needed_by, requested_by, note FROM stock_requests WHERE id = %s",
        (result.entity_id,),
    ).fetchone()
    assert row == ("open", 200, needed, world.users[IM].id, "for the Delhi order")
    assert result.events[0]["event"] == "stock_request_raised"
    assert result.events[0]["data"]["requested_by_name"] == "Demo Inventory Manager"


def test_stock_request_for_a_past_date_is_refused(world: World) -> None:
    with pytest.raises(WriteError, match="in the past"):
        preview(world.users[IM], "raise_stock_request",
                {"item": "TS-ZW-0002", "quantity": 1,
                 "needed_by": (app_today() - timedelta(days=1)).isoformat()})  # fmt: skip


def test_a_misspelt_name_offers_the_close_matches_instead_of_guessing(world: World) -> None:
    with pytest.raises(Ambiguous) as exc:
        preview(world.users[IM], "raise_stock_request",
                {"item": "Zeta Test Widgit Alfa", "quantity": 1})  # fmt: skip
    assert "Zeta Test Widget Alpha" in exc.value.options


def test_new_records_are_stamped_on_the_business_date(world: World) -> None:
    run(world, "record_stock_movement", kind="inbound", item="TS-ZW-0002", location="132-1",
        quantity=1)  # fmt: skip
    stamped = world.db.execute(
        "SELECT created_at::date FROM inv_transactions WHERE item_id = %s", (world.beta,)
    ).fetchone()
    assert stamped == (app_today(),)
