"""The write framework's guards (WRITE_PATH.md §2, §6): preview writes nothing, apply writes
exactly what was previewed, once, atomically, by someone still allowed to."""

import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from pydantic import BaseModel

from app.auth.passwords import hash_password
from app.auth.service import AuthUser
from app.services.writes import REGISTRY, WriteError, apply, cancel, preview
from app.services.writes.framework import (
    Action,
    NotPermitted,
    Outcome,
    Plan,
    PreviewExpired,
    PreviewMismatch,
    StalePreview,
)
from tests.integration.conftest import World

IM, PM, OW = "inventory_manager", "procurement_manager", "owner"


def op() -> str:
    return f"test-{uuid.uuid4()}"


def out(world: World, qty: float, role: str = IM) -> Any:
    args = {"kind": "outbound", "item": "TS-ZW-0001", "location": "132-1", "quantity": qty}
    return preview(world.users[role], "record_stock_movement", args)


# ------------------------------------------------------------------ preview
def test_preview_describes_the_change_and_writes_nothing(world: World) -> None:
    p = out(world, 4)
    assert p.summary == "Remove 4 pcs of Zeta Test Widget Alpha from 132-1."
    assert p.effects == ["132-1: 10 pcs → 6 pcs"]
    assert len(p.preview_hash) == 64 and len(p.state_version) == 64
    assert timedelta(minutes=9) < p.expires_at - datetime.now(UTC) <= timedelta(minutes=10)
    assert world.transactions(world.alpha) == [] and world.stock(world.alpha, "132-1") == 10


# ------------------------------------------------------------------ apply
def test_apply_writes_once_and_audits(world: World) -> None:
    p, op_id = out(world, 4), op()
    result = apply(world.users[IM], p, p.preview_hash, op_id, thread_id="t-1")
    assert result.status == "success" and not result.replayed
    assert result.message == ("Done: 4 pcs of Zeta Test Widget Alpha removed from 132-1. "
                              "132-1 now has 6 pcs.")  # fmt: skip
    assert world.stock(world.alpha, "132-1") == 6
    status, operation, entity_type, entity_id, before, after, error, role = world.audit(op_id)
    assert (status, operation, entity_type, role, error) == (
        "success", "record_stock_movement", "stock", IM, None)  # fmt: skip
    assert entity_id == result.entity_id
    assert before == {"stock": {world.loc["132-1"]: 10.0}}
    assert after["stock"] == {world.loc["132-1"]: 6.0}


def test_second_confirm_replays_the_first_result(world: World) -> None:
    p, op_id = out(world, 4), op()
    first = apply(world.users[IM], p, p.preview_hash, op_id)
    again = apply(world.users[IM], p, p.preview_hash, op_id)
    assert again.replayed and again.message == first.message
    assert len(world.transactions(world.alpha)) == 1 and world.stock(world.alpha, "132-1") == 6


def test_changed_payload_or_wrong_hash_is_refused(world: World) -> None:
    p = out(world, 4)
    tampered = p.model_copy(update={"payload": {**p.payload, "quantity": 1.0}})
    for preview_, confirmed in ((p, "0" * 64), (tampered, p.preview_hash),
                                (tampered, tampered.preview_hash)):  # fmt: skip
        with pytest.raises(PreviewMismatch):
            apply(world.users[IM], preview_, confirmed, op())
    assert world.transactions(world.alpha) == []


def test_another_users_preview_cant_be_applied(world: World) -> None:
    p = out(world, 4, role=PM)
    with pytest.raises((PreviewMismatch, NotPermitted)):
        apply(world.users[OW], p, p.preview_hash, op())
    assert world.transactions(world.alpha) == []


def test_expired_preview_is_refused_and_audited(world: World) -> None:
    p = out(world, 4)
    old = p.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
    op_id = op()
    with pytest.raises(PreviewExpired):
        apply(world.users[IM], old, old.preview_hash, op_id)
    status, *_, error, _ = world.audit(op_id)
    assert status == "failed" and "expired" in error
    assert world.stock(world.alpha, "132-1") == 10


def test_stale_preview_is_refused(world: World) -> None:
    first, second = out(world, 4), out(world, 3)
    apply(world.users[IM], first, first.preview_hash, op())
    op_id = op()
    with pytest.raises(StalePreview):
        apply(world.users[IM], second, second.preview_hash, op_id)
    assert world.stock(world.alpha, "132-1") == 6 and world.audit(op_id)[0] == "failed"


def test_parallel_confirms_never_take_stock_below_zero(world: World) -> None:
    a, b = out(world, 6), out(world, 6)  # each fits alone, not together
    results: list[str] = []

    def run(p: Any) -> None:
        try:
            apply(world.users[IM], p, p.preview_hash, op())
            results.append("ok")
        except WriteError as exc:
            results.append(exc.code)

    threads = [threading.Thread(target=run, args=(p,)) for p in (a, b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["ok", "stale"]
    assert world.stock(world.alpha, "132-1") == 4


def test_cancel_is_audited_and_changes_nothing(world: World) -> None:
    p, op_id = out(world, 4), op()
    cancel(world.users[IM], p, op_id)
    assert world.audit(op_id)[0] == "cancelled" and world.stock(world.alpha, "132-1") == 10
    with pytest.raises(WriteError, match="cancelled"):
        apply(world.users[IM], p, p.preview_hash, op_id)  # a late confirm can't revive it


# ------------------------------------------------------------------ rollback
class _Args(BaseModel):
    pass


class Boom(Action):
    """Writes a stock row, then fails: the transaction must leave no trace."""

    name = "transfer_stock"
    entity_type = "stock"
    args_model = _Args

    def __init__(self, item_id: str, location_id: str) -> None:
        self.item_id, self.location_id = item_id, location_id

    def plan(self, conn: psycopg.Connection, user: AuthUser, args: Any) -> Plan:
        return Plan(payload={}, summary="boom", fields=[], effects=[])

    def read_state(self, conn: psycopg.Connection, payload: dict, lock: bool) -> dict:
        return {}

    def apply(
        self, conn: psycopg.Connection, user: AuthUser, payload: dict, state: dict
    ) -> Outcome:
        conn.execute(
            """INSERT INTO inv_transactions (id, item_id, location_id, transaction_type, quantity,
                   created_at) VALUES (%s, %s, %s, 'inbound', 5, localtimestamp)""",
            (str(uuid.uuid4()), self.item_id, self.location_id),
        )
        raise RuntimeError("disk full (internal detail)")


def test_error_mid_transaction_rolls_everything_back(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(REGISTRY, "transfer_stock", Boom(world.alpha, world.loc["132-1"]))
    p, op_id = preview(world.users[IM], "transfer_stock", {}), op()
    with pytest.raises(WriteError) as exc:
        apply(world.users[IM], p, p.preview_hash, op_id)
    assert "No changes were made" in exc.value.message and "disk" not in exc.value.message
    assert world.transactions(world.alpha) == []
    status, *_, error, _ = world.audit(op_id)
    assert status == "failed" and "disk" not in error


# ------------------------------------------------------------------ authorisation
class ManagerOnly(Action):
    name = "create_vendor"  # managers only (policy)
    entity_type = "vendor"
    args_model = _Args

    def plan(self, conn: psycopg.Connection, user: AuthUser, args: Any) -> Plan:
        return Plan(payload={"x": 1}, summary="noop", fields=[], effects=[])

    def read_state(self, conn: psycopg.Connection, payload: dict, lock: bool) -> dict:
        return {}

    def apply(
        self, conn: psycopg.Connection, user: AuthUser, payload: dict, state: dict
    ) -> Outcome:
        return Outcome(message="ok", entity_id=None, after={})


@pytest.fixture
def temp_pm(world: World) -> Any:
    user_id = uuid.uuid4()
    world.db.execute(
        """INSERT INTO users (id, email, password_hash, full_name, role)
           VALUES (%s, %s, %s, 'Temp PM', 'procurement_manager')""",
        (user_id, f"temp-pm-{user_id}@test.inventorymantra", hash_password("x" * 12)),
    )
    yield AuthUser(id=user_id, email="t", full_name="Temp PM", role=PM, is_active=True,
                   token_version=0)  # fmt: skip
    world.db.execute("DELETE FROM agent_audit_log WHERE user_id = %s", (user_id,))
    world.db.execute("DELETE FROM users WHERE id = %s", (user_id,))


def test_policy_is_checked_at_preview(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(REGISTRY, "create_vendor", ManagerOnly())
    with pytest.raises(NotPermitted):
        preview(world.users[IM], "create_vendor", {})


@pytest.mark.parametrize("change", ["deactivate", "demote"])
def test_policy_is_rechecked_at_apply(
    world: World, temp_pm: AuthUser, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    monkeypatch.setitem(REGISTRY, "create_vendor", ManagerOnly())
    p = preview(temp_pm, "create_vendor", {})
    sql = ("UPDATE users SET is_active = false WHERE id = %s" if change == "deactivate"
           else "UPDATE users SET role = 'inventory_manager' WHERE id = %s")  # fmt: skip
    world.db.execute(sql, (temp_pm.id,))
    op_id = op()
    with pytest.raises(NotPermitted):
        apply(temp_pm, p, p.preview_hash, op_id)
    assert world.audit(op_id)[0] == "failed"


def test_unknown_action_and_bad_arguments_get_plain_messages(world: World) -> None:
    with pytest.raises(WriteError, match="can't do that"):
        preview(world.users[OW], "drop_table", {})
    with pytest.raises(WriteError, match="missing or invalid: kind, quantity"):
        preview(world.users[IM], "record_stock_movement", {"item": "x", "location": "132-1"})
