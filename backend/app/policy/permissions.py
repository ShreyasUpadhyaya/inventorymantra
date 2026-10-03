"""Who may read which data areas and run which actions (plan.md §1, WRITE_PATH.md §4).

Single source of truth for authorisation decisions in the API and the agent. Default deny:
unknown roles, areas and actions are refused. Role names are matched exactly.
"""

from collections.abc import Iterable

from app.core.config import get_settings

IM, PM, OWNER = "inventory_manager", "procurement_manager", "owner"
ALL = frozenset({IM, PM, OWNER})
MANAGERS = frozenset({PM, OWNER})
OWNER_ONLY = frozenset({OWNER})

# Data areas, as named in the guard's `data_areas` output (PROMPTS.md §2).
READ_AREAS: dict[str, frozenset[str]] = {
    "stock": ALL,
    "movements": ALL,
    "inbound": ALL,  # IM sees PO number, qty and date only (v_im.inbound_by_item)
    "requests": ALL,  # IM sees own requests (v_im.my_stock_requests)
    "reorder": ALL,  # IM sees it without vendor
    "vendors": MANAGERS,
    "purchase_orders": MANAGERS,
    "prices": MANAGERS,
    "payments": MANAGERS,
    "spend": MANAGERS,
    "audit_own": MANAGERS,
    "sales": OWNER_ONLY,
    "margins": OWNER_ONLY,
    "users": OWNER_ONLY,
    "approvals": OWNER_ONLY,
    "audit_all": OWNER_ONLY,
}

# Write actions (WRITE_PATH.md §4).
ACTIONS: dict[str, frozenset[str]] = {
    "record_stock_movement": ALL,
    "transfer_stock": ALL,
    "receive_against_po": ALL,
    "record_unexpected_receipt": ALL,
    "raise_stock_request": ALL,
    "create_purchase_order": MANAGERS,
    "update_po_expected_date": MANAGERS,
    "cancel_po": MANAGERS,
    "clear_payment_tranche": MANAGERS,
    "set_reorder_policy": MANAGERS,
    "create_vendor": MANAGERS,
    "decide_approval": OWNER_ONLY,
    "create_user": OWNER_ONLY,
    "deactivate_user": OWNER_ONLY,
    "reset_password": OWNER_ONLY,
}


def can_read(role: str, area: str) -> bool:
    return role in READ_AREAS.get(area, frozenset())


def can(role: str, action: str) -> bool:
    return role in ACTIONS.get(action, frozenset())


def denied_areas(role: str, areas: Iterable[str]) -> set[str]:
    """For mixed requests: the areas to deny while answering the rest (D-020)."""
    return {area for area in areas if not can_read(role, area)}


def requires_approval(role: str, po_total: float) -> bool:
    """A Procurement Manager's PO above the threshold waits for the Owner (D-007)."""
    return role == PM and po_total > get_settings().po_approval_threshold_inr
