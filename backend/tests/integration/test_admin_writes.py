"""Owner actions (C6.4): decide a PO waiting for approval, add and deactivate users, and the
Approvals API."""

# ruff: noqa: F811  (fixtures imported from test_users are used as parameters)

import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.services.writes import WriteError, apply, preview
from app.services.writes.framework import NotPermitted, StalePreview
from tests.api.test_users import (  # noqa: F401
    DOMAIN,
    _requires_jwt_secret,
    make_user,
    owner,
    session,
)
from tests.integration.conftest import World
from tests.integration.test_procurement_writes import number_of, place, po_row, run

IM, PM, OW = "inventory_manager", "procurement_manager", "owner"


def as_owner(world: World, action: str, **args: Any) -> tuple[Any, Any]:
    p = preview(world.users[OW], action, args)
    return p, apply(world.users[OW], p, p.preview_hash, f"test-{uuid.uuid4()}")


def waiting(world: World, with_request: bool = True) -> tuple[str, str, Any]:
    """A PM order over the limit (waiting for the Owner), optionally covering an IM request."""
    req = None
    if with_request:
        _, req = run(world, "raise_stock_request", IM, item="TS-ZW-0001", quantity=40)
    _, po = place(world, role=PM, price=2500)
    return po.entity_id, number_of(world, po.entity_id), req


def approval(world: World, po_id: str) -> tuple:
    return world.db.execute(
        "SELECT status, decided_by, note FROM approvals WHERE entity_id = %s", (po_id,)
    ).fetchone()


# ------------------------------------------------------------------ decide
def test_owner_approves_and_everyone_concerned_is_told(world: World) -> None:
    po_id, number, req = waiting(world)
    p, result = run(world, "decide_approval", OW, po=number, decision="approve")
    assert p.summary == f"Approve {number} for ₹1,48,000."
    assert "Demo Procurement Manager is told the decision." in p.effects
    assert "The linked stock request is marked as ordered." in p.effects
    assert po_row(world, po_id)[1] == "placed"
    assert approval(world, po_id)[:2] == ("approved", world.users[OW].id)
    status = world.db.execute("SELECT status FROM stock_requests WHERE id = %s",
                              (req.entity_id,)).fetchone()  # fmt: skip
    assert status == ("ordered",)
    targets = [e["to"]["users"] for e in result.events]
    assert targets == [[str(world.users[PM].id)], [str(world.users[IM].id)]]
    assert result.message == (f"Done: {number} (₹1,48,000) is approved and placed. "
                              "Demo Procurement Manager has been told.")  # fmt: skip


def test_owner_rejects_with_a_note_and_the_request_reopens(world: World) -> None:
    po_id, number, req = waiting(world)
    run(world, "decide_approval", OW, po=number, decision="reject", note="Too expensive")
    assert po_row(world, po_id)[1] == "rejected"
    assert approval(world, po_id) == ("rejected", world.users[OW].id, "Too expensive")
    row = world.db.execute("SELECT status, po_id FROM stock_requests WHERE id = %s",
                           (req.entity_id,)).fetchone()  # fmt: skip
    assert row == ("open", None)


def test_only_waiting_pos_and_only_the_owner(world: World) -> None:
    _, placed = place(world, role=OW)
    with pytest.raises(WriteError, match="isn't waiting for your approval"):
        preview(world.users[OW], "decide_approval",
                {"po": number_of(world, placed.entity_id), "decision": "approve"})  # fmt: skip
    _, number, _ = waiting(world, with_request=False)
    for role in (PM, IM):
        with pytest.raises(NotPermitted):
            preview(world.users[role], "decide_approval", {"po": number, "decision": "approve"})


def test_two_decisions_on_one_po_only_the_first_counts(world: World) -> None:
    _, number, _ = waiting(world, with_request=False)
    yes = preview(world.users[OW], "decide_approval", {"po": number, "decision": "approve"})
    no = preview(world.users[OW], "decide_approval", {"po": number, "decision": "reject"})
    apply(world.users[OW], yes, yes.preview_hash, f"test-{uuid.uuid4()}")
    with pytest.raises(StalePreview):
        apply(world.users[OW], no, no.preview_hash, f"test-{uuid.uuid4()}")


# ------------------------------------------------------------------ users
@pytest.fixture
def cleanup_users(world: World) -> Iterator[None]:
    yield
    rows = world.db.execute(
        "SELECT id FROM users WHERE email LIKE 'zeta-%@test.inventorymantra'"
    ).fetchall()
    ids = [r[0] for r in rows]
    world.db.execute("DELETE FROM agent_audit_log WHERE user_id = ANY(%s)", (ids,))
    world.db.execute("DELETE FROM refresh_tokens WHERE user_id = ANY(%s)", (ids,))
    world.db.execute("DELETE FROM notifications WHERE recipient_user_id = ANY(%s)", (ids,))
    world.db.execute("DELETE FROM users WHERE id = ANY(%s)", (ids,))


@pytest.mark.usefixtures("cleanup_users")
def test_owner_adds_a_user_without_a_password_passing_through(world: World) -> None:
    p, result = as_owner(world, "create_user", full_name="Zeta Tester",
                    email="Zeta-Tester@Test.InventoryMantra", role="inventory_manager")  # fmt: skip
    assert p.summary == "Add Zeta Tester (zeta-tester@test.inventorymantra) as Inventory Manager."
    row = world.db.execute(
        "SELECT role, must_change_password, created_by FROM users WHERE id = %s",
        (result.entity_id,),
    ).fetchone()
    assert row == ("inventory_manager", True, world.users[OW].id)
    assert "Reset password" in result.message
    audit = world.db.execute("SELECT after_state FROM agent_audit_log WHERE entity_id = %s",
                             (result.entity_id,)).fetchone()  # fmt: skip
    assert audit is not None and set(audit[0]) == {"email", "role", "_result"}  # no secret
    with pytest.raises(WriteError, match="already exists"):
        preview(world.users[OW], "create_user",
                {"full_name": "Again", "email": "zeta-tester@test.inventorymantra",
                 "role": "owner"})  # fmt: skip


@pytest.mark.usefixtures("cleanup_users")
def test_owner_deactivates_a_user_by_email(world: World) -> None:
    _, created = as_owner(
        world, "create_user", full_name="Zeta Leaver", email="zeta-leaver@test.inventorymantra",
        role="procurement_manager",
    )  # fmt: skip
    p, _ = as_owner(world, "deactivate_user", user="zeta-leaver@test.inventorymantra")
    assert p.summary == "Deactivate Zeta Leaver (zeta-leaver@test.inventorymantra)."
    active = world.db.execute("SELECT is_active FROM users WHERE id = %s",
                              (created.entity_id,)).fetchone()  # fmt: skip
    assert active == (False,)
    with pytest.raises(WriteError, match="already deactivated"):
        preview(world.users[OW], "deactivate_user", {"user": "zeta-leaver@test.inventorymantra"})
    with pytest.raises(WriteError, match="your own account"):
        preview(world.users[OW], "deactivate_user", {"user": "owner@demo.inventorymantra"})


# ------------------------------------------------------------------ Approvals API
def test_approvals_api(
    owner: TestClient, make_user: Any, world: World, db: psycopg.Connection
) -> None:
    """`owner` comes first so it is torn down after `world` removes the approvals."""
    po_id, number, _ = waiting(world, with_request=False)
    listed = owner.get("/approvals")
    assert listed.status_code == 200
    [row] = [r for r in listed.json() if r["po_number"] == number]
    assert row["total_amount"] == 148000 and row["requested_by_name"] == "Demo Procurement Manager"
    assert row["lines"][0]["item_name"] == "Zeta Test Widget Alpha"
    for role, name in ((IM, "zim"), (PM, "zpm")):
        make_user(role, name)
        client = session(name)
        assert client.get("/approvals").status_code == 403
        assert (
            client.post(f"/approvals/{number}/decide", json={"decision": "approve"}).status_code
            == 403
        )
    done = owner.post(f"/approvals/{number}/decide", json={"decision": "approve", "note": "ok"})
    assert done.status_code == 200 and "approved and placed" in done.json()["message"]
    assert po_row(world, po_id)[1] == "placed"
    again = owner.post(f"/approvals/{number}/decide", json={"decision": "reject"})
    assert again.status_code == 409 and "isn't waiting" in again.json()["detail"]
    assert owner.post(f"/approvals/{number}/decide", json={"decision": "maybe"}).status_code == 422


# ------------------------------------------------------------------ ask / reply (C6.6)
def messages(world: World, po_id: str) -> list[tuple]:
    return world.db.execute(
        """SELECT m.kind, m.body, m.author_id FROM approval_messages m
           JOIN approvals a ON a.id = m.approval_id WHERE a.entity_id = %s
           ORDER BY m.created_at""",
        (po_id,),
    ).fetchall()


def test_owner_asks_the_requester_replies_and_the_po_keeps_waiting(world: World) -> None:
    po_id, number, _ = waiting(world, with_request=False)
    p, asked = as_owner(world, "ask_about_approval", po=number,
                        message="Why Zeta and not Shakti?")  # fmt: skip
    assert p.summary == f"Ask Demo Procurement Manager about {number}."
    assert asked.events[0]["to"] == {"users": [str(world.users[PM].id)]}
    reply = preview(world.users[PM], "reply_to_approval",
                    {"po": number, "message": "Shakti can't deliver before November"})  # fmt: skip
    done = apply(world.users[PM], reply, reply.preview_hash, f"test-{uuid.uuid4()}")
    assert done.message == f"Done: your reply about {number} was sent to the Owner."
    assert [(k, b) for k, b, _ in messages(world, po_id)] == [
        ("question", "Why Zeta and not Shakti?"),
        ("reply", "Shakti can't deliver before November"),
    ]
    assert po_row(world, po_id)[1] == "pending_approval"  # still the Owner's decision
    as_owner(world, "decide_approval", po=number, decision="approve")
    with pytest.raises(WriteError, match="isn't waiting for approval"):
        as_owner(world, "ask_about_approval", po=number, message="too late")


def test_only_the_requester_may_reply_and_only_the_owner_may_ask(
    world: World, make_user: Any
) -> None:
    _, number, _ = waiting(world, with_request=False)
    other_pm = make_user(PM, "zpm2")
    from app.auth.service import load_user

    stranger = load_user(world.db, other_pm)
    with pytest.raises(WriteError, match="only reply about your own"):
        preview(stranger, "reply_to_approval", {"po": number, "message": "hi"})
    for role in (PM, IM):
        with pytest.raises(NotPermitted):
            preview(world.users[role], "ask_about_approval", {"po": number, "message": "?"})
    with pytest.raises(NotPermitted):
        preview(world.users[IM], "reply_to_approval", {"po": number, "message": "?"})


def test_ask_and_reply_through_the_api(owner: TestClient, make_user: Any, world: World) -> None:
    po_id, number, _ = waiting(world, with_request=False)
    assert (
        owner.post(f"/approvals/{number}/ask", json={"message": "Any cheaper vendor?"}).status_code
        == 200
    )
    [row] = [r for r in owner.get("/approvals").json() if r["po_number"] == number]
    assert [m["kind"] for m in row["messages"]] == ["question"]
    make_user(IM, "zim3")
    assert session("zim3").get("/approvals/mine").status_code == 403
    assert owner.post(f"/approvals/{number}/ask", json={"message": ""}).status_code == 422
