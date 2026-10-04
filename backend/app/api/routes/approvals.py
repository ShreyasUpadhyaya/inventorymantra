"""Owner approvals (C6.4): purchase orders waiting for a decision, and the decision itself.

GET  /approvals                      pending POs with their lines (Owner only)
POST /approvals/{po_number}/decide   {decision: approve|reject, note?}

The decision runs through the write framework (decide_approval): the same checks, one
transaction and the same audit row as the chat. The button click on the Approvals page,
after its own "are you sure", is the confirmation, so preview and apply run back to back.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import Conn, require_action
from app.auth.service import AuthUser
from app.services.writes import WriteError, apply, preview
from app.services.writes.framework import NotPermitted

router = APIRouter(prefix="/approvals", tags=["approvals"])
CanDecide = Annotated[AuthUser, Depends(require_action("decide_approval"))]


class ApprovalLine(BaseModel):
    item_name: str
    quantity: float
    unit: str
    unit_price: float
    line_total: float


class PendingApproval(BaseModel):
    po_number: str
    vendor_name: str | None
    total_amount: float
    requested_by_name: str
    requested_at: datetime
    expected_delivery_date: str | None
    lines: list[ApprovalLine]


class DecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=300)


class DecisionOut(BaseModel):
    message: str


@router.get("", response_model=list[PendingApproval])
def pending(_: CanDecide, conn: Conn) -> list[PendingApproval]:
    rows = conn.execute(
        """SELECT p.id, p.po_number, v.name, p.total_amount::float, u.full_name, a.created_at,
                  (SELECT max(d::date) FROM json_array_elements_text(
                       CASE WHEN json_typeof(p.expected_delivery_date) = 'array'
                            THEN p.expected_delivery_date END) d)::text
           FROM approvals a
           JOIN proc_purchase_orders p ON a.entity_type = 'purchase_order' AND p.id = a.entity_id
           JOIN users u ON u.id = a.requested_by
           LEFT JOIN proc_vendors v ON v.id = p.vendor_id
           WHERE a.status = 'pending' ORDER BY a.created_at"""
    ).fetchall()
    out = []
    for po_id, number, vendor, total, who, created, expected in rows:
        lines = conn.execute(
            """SELECT i.name, l.quantity_ordered::float, coalesce(i.unit, l.unit, 'pcs'),
                      l.unit_price::float, l.line_total::float
               FROM proc_po_lines l JOIN inv_items i ON i.id = l.inv_item_id
               WHERE l.po_id = %s ORDER BY l.position""",
            (po_id,),
        ).fetchall()
        out.append(PendingApproval(
            po_number=number, vendor_name=vendor, total_amount=total, requested_by_name=who,
            requested_at=created, expected_delivery_date=expected,
            lines=[ApprovalLine(item_name=n, quantity=q, unit=u, unit_price=p, line_total=t)
                   for n, q, u, p, t in lines],
        ))  # fmt: skip
    return out


@router.post("/{po_number}/decide", response_model=DecisionOut)
def decide(po_number: str, body: Annotated[DecideIn, Body()], owner: CanDecide) -> DecisionOut:
    args = {"po": po_number, "decision": body.decision, "note": body.note}
    try:
        card = preview(owner, "decide_approval", args)
        result = apply(owner, card, card.preview_hash, f"api:{uuid.uuid4()}")
    except NotPermitted as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.message) from None
    except WriteError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.message) from None
    return DecisionOut(message=result.message)
