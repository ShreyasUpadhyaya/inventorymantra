"""Policy contract: who may read which data areas and run which write actions.

Mirrors plan.md §1 (role responsibilities) and docs/WRITE_PATH.md §4 (action catalogue).
Implementation: app/policy/permissions.py (C3.2).
"""

from types import ModuleType

import pytest

IM, PM, OW = "inventory_manager", "procurement_manager", "owner"
ROLES = [IM, PM, OW]

# Data areas, as named in the guard's `data_areas` output (PROMPTS.md §2).
READ_MATRIX = {
    "stock": {IM, PM, OW},
    "movements": {IM, PM, OW},
    "inbound": {IM, PM, OW},
    "requests": {IM, PM, OW},
    "reorder": {IM, PM, OW},
    "vendors": {PM, OW},
    "purchase_orders": {PM, OW},
    "prices": {PM, OW},
    "payments": {PM, OW},
    "spend": {PM, OW},
    "audit_own": {PM, OW},
    "sales": {OW},
    "margins": {OW},
    "users": {OW},
    "approvals": {OW},
    "audit_all": {OW},
}

# Write actions, as named in WRITE_PATH.md §4.
WRITE_MATRIX = {
    "record_stock_movement": {IM, PM, OW},
    "transfer_stock": {IM, PM, OW},
    "receive_against_po": {IM, PM, OW},
    "record_unexpected_receipt": {IM, PM, OW},
    "raise_stock_request": {IM, PM, OW},
    "create_purchase_order": {PM, OW},
    "update_po_expected_date": {PM, OW},
    "cancel_po": {PM, OW},
    "clear_payment_tranche": {PM, OW},
    "set_reorder_policy": {PM, OW},
    "create_vendor": {PM, OW},
    "decide_approval": {OW},
    "create_user": {OW},
    "deactivate_user": {OW},
}


def permissions() -> ModuleType:
    from app.policy import permissions as module

    return module


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("area", sorted(READ_MATRIX))
def test_read_areas(role: str, area: str) -> None:
    assert permissions().can_read(role, area) is (role in READ_MATRIX[area])


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("action", sorted(WRITE_MATRIX))
def test_write_actions(role: str, action: str) -> None:
    assert permissions().can(role, action) is (role in WRITE_MATRIX[action])


def test_matrices_cover_every_known_area_and_action() -> None:
    p = permissions()
    assert set(p.READ_AREAS) == set(READ_MATRIX)
    assert set(p.ACTIONS) == set(WRITE_MATRIX)


@pytest.mark.parametrize(
    ("role", "thing"),
    [(IM, "drop_table"), (OW, "drop_table"), (IM, ""), ("hacker", "stock"), ("OWNER", "sales")],
)
def test_unknown_roles_actions_and_areas_are_denied(role: str, thing: str) -> None:
    p = permissions()
    assert p.can(role, thing) is False
    assert p.can_read(role, thing) is False


def test_denied_areas_splits_mixed_requests() -> None:
    """Mixed requests: answer the permitted part, deny the rest (D-020)."""
    assert permissions().denied_areas(IM, ["stock", "prices"]) == {"prices"}
    assert permissions().denied_areas(PM, ["purchase_orders", "margins", "users"]) == {
        "margins",
        "users",
    }
    assert permissions().denied_areas(OW, ["margins", "users"]) == set()


@pytest.mark.parametrize(
    ("role", "po_total", "needed"),
    [
        (PM, 100_000, False),  # threshold itself does not need approval ("above")
        (PM, 100_000.01, True),
        (PM, 2_70_000, True),
        (PM, 1_250, False),
        (OW, 50_00_000, False),  # the Owner's own POs are auto-approved
    ],
)
def test_po_approval_threshold(role: str, po_total: float, needed: bool) -> None:
    assert permissions().requires_approval(role, po_total) is needed
