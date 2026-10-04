"""Writes in the chat (C6.3): one question at a time, one confirm card, then all-or-nothing.

Offline (the fake write stand-in or scripted model output) on the seeded DB, with the
throwaway items and vendor from tests/integration/conftest.py.
"""

# ruff: noqa: F811  (the `world` fixture is imported and used as a parameter)

from typing import Any

import pytest

from app.agent import fake
from app.agent.nodes.write import WriteCallOut, parse_date, parse_number
from app.agent.prompts import DENIAL
from app.agent.run import TurnResult, resume_turn, run_turn
from app.core.config import app_today
from tests.integration.conftest import World, world  # noqa: F401

IM, PM, OW = "inventory_manager", "procurement_manager", "owner"


def ask(world: World, role: str, text: str) -> TurnResult:
    return run_turn(world.users[role], text)


def answer(world: World, role: str, turn: TurnResult, text: str) -> TurnResult:
    return resume_turn(world.users[role], turn.thread_id, text)


def call(action: str, **fields: Any) -> dict[str, Any]:
    return {**dict.fromkeys(WriteCallOut.model_fields), "action": action, **fields}


def audit_rows(world: World, thread_id: str) -> list[tuple]:
    return world.db.execute(
        "SELECT operation_id, status, operation FROM agent_audit_log "
        "WHERE operation_id LIKE %s ORDER BY operation_id",
        (f"chat:{thread_id}:%",),
    ).fetchall()


# ------------------------------------------------------------------ happy path
def test_a_complete_request_goes_straight_to_one_confirm_card(world: World) -> None:
    turn = ask(world, IM, "Add 3 Zeta Test Widget Beta to 132-2")
    assert turn.route == "confirm" and turn.interrupt is not None
    assert turn.interrupt["kind"] == "confirm" and turn.suggestions == ["Confirm", "Cancel"]
    [card] = turn.interrupt["previews"]
    assert card["summary"] == "Add 3 pcs of Zeta Test Widget Beta to 132-2."
    assert card["effects"] == ["132-2: 0 pcs → 3 pcs"]
    assert world.stock(world.beta, "132-2") is None  # nothing saved before confirming
    done = answer(world, IM, turn, "Confirm")
    assert done.route == "write" and done.interrupt is None
    assert (
        done.answer == "Done: 3 pcs of Zeta Test Widget Beta added to 132-2. 132-2 now has 3 pcs."
    )
    assert world.stock(world.beta, "132-2") == 3
    assert [r[1:] for r in audit_rows(world, turn.thread_id)] == [
        ("success", "record_stock_movement")
    ]


def test_hinglish_request(world: World) -> None:
    turn = ask(world, IM, "132-2 mein 4 Zeta Test Widget Beta add kar do")
    assert turn.route == "confirm" and turn.lang == "hinglish"
    assert turn.suggestions == ["Confirm", "Cancel"]
    assert answer(world, IM, turn, "haan").answer.startswith("Done: 4 pcs")


# ------------------------------------------------------------------ the user's example
def test_vague_two_part_request_asks_one_question_at_a_time(world: World) -> None:
    """'add 3 to 132 and 5 to the other': no item, an unclear warehouse, a pointer word."""
    turn = ask(world, IM, "add 3 to 132 and 5 to the other")
    assert (turn.route, turn.answer, turn.suggestions) == ("clarify", "Which item?", [])
    turn = answer(world, IM, turn, "Zeta Test Widget Alpha")
    assert turn.answer == "“132” matches more than one record. Which one do you mean?"
    assert turn.suggestions == ["132-1", "132-2", "132-3"]
    turn = answer(world, IM, turn, "132-2")
    assert turn.answer == "Which warehouse?"  # "the other" names nothing: asked, not guessed
    assert "132-3" in turn.suggestions and "Unassigned" not in turn.suggestions
    turn = answer(world, IM, turn, "132-3")
    assert turn.route == "confirm"
    assert [c["summary"] for c in turn.interrupt["previews"]] == [
        "Add 3 pcs of Zeta Test Widget Alpha to 132-2.",
        "Add 5 pcs of Zeta Test Widget Alpha to 132-3.",
    ]
    done = answer(world, IM, turn, "Confirm")
    assert world.stock(world.alpha, "132-2") == 3 and world.stock(world.alpha, "132-3") == 5
    assert done.answer.count("Done:") == 2
    rows = audit_rows(world, turn.thread_id)
    assert [r[0][-2:] for r in rows] == ["#1", "#2"] and {r[1] for r in rows} == {"success"}


# ------------------------------------------------------------------ cancel, refusals
@pytest.mark.parametrize("reply", ["Cancel", "no", "maybe later"])
def test_anything_but_a_clear_yes_cancels(world: World, reply: str) -> None:
    turn = ask(world, IM, "Add 3 Zeta Test Widget Beta to 132-2")
    done = answer(world, IM, turn, reply)
    assert done.answer == "Cancelled. Nothing was changed."
    assert world.stock(world.beta, "132-2") is None
    assert [r[1] for r in audit_rows(world, turn.thread_id)] == ["cancelled"]


def test_not_enough_stock_is_explained_and_nothing_is_saved(world: World) -> None:
    turn = ask(world, IM, "Issue 10000 Zeta Test Widget Alpha from 132-1 to H103 Shop Floor")
    assert turn.route == "write" and turn.interrupt is None
    assert "132-1 has only 10 pcs of Zeta Test Widget Alpha" in turn.answer
    assert world.transactions(world.alpha) == []


def test_unusable_answers_are_asked_again_once_then_dropped(world: World) -> None:
    plan = {"actions": [call("record_stock_movement", kind="inbound",
                             item="Zeta Test Widget Beta", location="132-2")],
            "none_applicable": False}  # fmt: skip
    with fake.scripted("write", plan):
        turn = ask(world, IM, "add some beta widgets to 132-2")
    assert turn.answer == "How many of Zeta Test Widget Beta?"
    turn = answer(world, IM, turn, "lots")
    assert turn.answer == "I didn't catch a number. How many of Zeta Test Widget Beta?"
    done = answer(world, IM, turn, "a few")
    assert done.answer.startswith("I still couldn't work out the quantity, so nothing was changed")
    assert world.transactions(world.beta) == []


def test_inventory_manager_cant_place_a_po_in_chat(world: World) -> None:
    turn = ask(
        world, IM, "Place a PO for 50 Zeta Test Widget Alpha from Zeta Test Supplies at ₹10 each"
    )
    assert turn.answer == DENIAL["en"] and turn.interrupt is None


def test_an_action_outside_the_role_is_refused_even_if_the_model_picks_it(world: World) -> None:
    plan = {"actions": [call("create_vendor", name="Zeta Test Bolts")], "none_applicable": False}
    with fake.scripted("write", plan):
        turn = ask(world, IM, "add a vendor Zeta Test Bolts")
    assert turn.answer == DENIAL["en"]


def test_nothing_to_change(world: World) -> None:
    with fake.scripted("write", {"actions": [], "none_applicable": True}):
        turn = ask(world, PM, "add up the spend for me")
    assert turn.answer.startswith("Offline demo mode") and turn.interrupt is None


# ------------------------------------------------------------------ procurement in chat
def test_pm_order_over_the_limit_is_saved_waiting_for_the_owner(world: World) -> None:
    turn = ask(world, PM, "Place a PO for 60 Zeta Test Widget Alpha from Zeta Test Supplies "
                          "at ₹2,500 each")  # fmt: skip
    [card] = turn.interrupt["previews"]
    assert card["summary"] == ("Place a purchase order with Zeta Test Supplies for 1 line, "
                               "total ₹1,50,000.")  # fmt: skip
    assert any("waits for the Owner's approval" in e for e in card["effects"])
    done = answer(world, PM, turn, "Confirm")
    assert "waiting for the Owner's approval" in done.answer
    assert [r[1] for r in audit_rows(world, turn.thread_id)] == ["awaiting_approval"]


def test_missing_price_is_asked_not_invented(world: World) -> None:
    plan = {"actions": [call("create_purchase_order", vendor="Zeta Test Supplies",
                             lines=[{"item": "Zeta Test Widget Alpha", "quantity": 10,
                                     "unit_price": None, "tax_percentage": None}])],
            "none_applicable": False}  # fmt: skip
    with fake.scripted("write", plan):
        turn = ask(world, PM, "place an order for 10 alpha widgets from zeta test supplies")
    assert turn.answer == "What is the unit price of Zeta Test Widget Alpha, in ₹?"
    turn = answer(world, PM, turn, "₹1,250.50")
    [card] = turn.interrupt["previews"]
    assert "10 pcs × ₹1,250.50 = ₹12,505" in " ".join(f["value"] for f in card["fields"])


def test_receipt_card_for_the_inventory_manager_shows_no_price(world: World) -> None:
    owner_turn = ask(world, OW, "Place a PO for 20 Zeta Test Widget Beta from Zeta Test Supplies "
                                "at ₹99 each")  # fmt: skip
    answer(world, OW, owner_turn, "Confirm")
    number = world.db.execute(
        "SELECT po_number FROM proc_purchase_orders WHERE vendor_id = %s", (world.vendor,)
    ).fetchone()[0]
    turn = ask(world, IM, f"Receive 5 Zeta Test Widget Beta against {number} into 132-1")
    shown = str(turn.interrupt["previews"])
    assert turn.route == "confirm" and "₹" not in shown and "Zeta Test Supplies" not in shown
    assert answer(world, IM, turn, "Confirm").answer.startswith("Done: 5 pcs")


# ------------------------------------------------------------------ answer parsing
@pytest.mark.parametrize(
    ("text", "expected"),
    [("5", 5.0), ("5 pcs", 5.0), ("₹2,500.50", 2500.5), ("about 12", 12.0), ("lots", None)],
)
def test_numbers(text: str, expected: float | None) -> None:
    assert parse_number(text) == expected


def test_dates() -> None:
    today = app_today()
    assert parse_date("2026-10-15", today).isoformat() == "2026-10-15"
    assert parse_date("15/10/2026", today).isoformat() == "2026-10-15"
    assert parse_date("15 Oct", today).isoformat() == "2026-10-15"
    assert parse_date("Oct 15", today).isoformat() == "2026-10-15"
    assert parse_date("kal", today) is not None and parse_date("someday", today) is None
    assert parse_date("31/02/2026", today) is None
