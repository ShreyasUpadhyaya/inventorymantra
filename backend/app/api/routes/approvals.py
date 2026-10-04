"""Owner approvals (C6.4): purchase orders waiting for a decision, and the decision itself.

GET  /approvals                      pending POs with their lines and messages (Owner only)
POST /approvals/{po_number}/decide   {decision: approve|reject, note?}
POST /approvals/{po_number}/ask      {message}  the Owner asks the requester (C6.6)
GET  /approvals/mine                 the Procurement Manager's own requests and messages
POST /approvals/{po_number}/reply    {message}  the requester answers (own requests only)

The decision runs through the write framework (decide_approval): the same checks, one
transaction and the same audit row as the chat. The button click on the Approvals page,
after its own "are you sure", is the confirmation, so preview and apply run back to back.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import Conn, require_action
from app.auth.service import AuthUser
from app.services.writes import WriteError, apply, preview
from app.services.writes.framework import NotPermitted

router = APIRouter(prefix="/approvals", tags=["approvals"])
CanDecide = Annotated[AuthUser, Depends(require_action("decide_approval"))]
CanReply = Annotated[AuthUser, Depends(require_action("reply_to_approval"))]


class ApprovalLine(BaseModel):
    item_name: str
    quantity: float
    unit: str
    unit_price: float
    line_total: float


class ApprovalMessage(BaseModel):
    kind: Literal["question", "reply"]
    author_name: str
    body: str
    created_at: datetime


class PendingApproval(BaseModel):
    po_number: str
    status: str
    vendor_name: str | None
    total_amount: float
    requested_by_name: str
    requested_at: datetime
    expected_delivery_date: str | None
    lines: list[ApprovalLine]
    messages: list[ApprovalMessage]


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=500)


class DecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=300)


class DecisionOut(BaseModel):
    message: str


LIST_SQL = """
SELECT a.id, p.id, p.po_number, a.status, v.name, p.total_amount::float, u.full_name,
       a.created_at,
       (SELECT max(d::date) FROM json_array_elements_text(
            CASE WHEN json_typeof(p.expected_delivery_date) = 'array'
                 THEN p.expected_delivery_date END) d)::text
FROM approvals a
JOIN proc_purchase_orders p ON a.entity_type = 'purchase_order' AND p.id = a.entity_id
JOIN users u ON u.id = a.requested_by
LEFT JOIN proc_vendors v ON v.id = p.vendor_id
"""


def _listing(conn: Any, where: str, params: tuple[Any, ...]) -> list[PendingApproval]:
    rows = conn.execute(LIST_SQL + where, params).fetchall()
    out = []
    for approval_id, po_id, number, status_, vendor, total, who, created, expected in rows:
        lines = conn.execute(
            """SELECT i.name, l.quantity_ordered::float, coalesce(i.unit, l.unit, 'pcs'),
                      l.unit_price::float, l.line_total::float
               FROM proc_po_lines l JOIN inv_items i ON i.id = l.inv_item_id
               WHERE l.po_id = %s ORDER BY l.position""",
            (po_id,),
        ).fetchall()
        messages = conn.execute(
            """SELECT m.kind, u.full_name, m.body, m.created_at FROM approval_messages m
               JOIN users u ON u.id = m.author_id WHERE m.approval_id = %s
               ORDER BY m.created_at""",
            (approval_id,),
        ).fetchall()
        out.append(PendingApproval(
            po_number=number, status=status_, vendor_name=vendor, total_amount=total,
            requested_by_name=who, requested_at=created, expected_delivery_date=expected,
            lines=[ApprovalLine(item_name=n, quantity=q, unit=u, unit_price=p, line_total=t)
                   for n, q, u, p, t in lines],
            messages=[ApprovalMessage(kind=k, author_name=a, body=b, created_at=c)
                      for k, a, b, c in messages],
        ))  # fmt: skip
    return out


@router.get("", response_model=list[PendingApproval])
def pending(_: CanDecide, conn: Conn) -> list[PendingApproval]:
    return _listing(conn, "WHERE a.status = 'pending' ORDER BY a.created_at", ())


@router.get("/mine", response_model=list[PendingApproval])
def mine(user: CanReply, conn: Conn) -> list[PendingApproval]:
    """Your own requests: waiting ones first, then the latest decisions."""
    return _listing(
        conn,
        "WHERE a.requested_by = %s ORDER BY (a.status = 'pending') DESC, a.created_at DESC "
        "LIMIT 20",
        (user.id,),
    )


def _run(user: AuthUser, action: str, args: dict[str, Any]) -> DecisionOut:
    """Preview and apply back to back: the page's own confirm step was the confirmation."""
    try:
        card = preview(user, action, args)
        result = apply(user, card, card.preview_hash, f"api:{uuid.uuid4()}")
    except NotPermitted as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.message) from None
    except WriteError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.message) from None
    return DecisionOut(message=result.message)


@router.post("/{po_number}/ask", response_model=DecisionOut)
def ask(po_number: str, body: Annotated[MessageIn, Body()], owner: CanDecide) -> DecisionOut:
    return _run(owner, "ask_about_approval", {"po": po_number, "message": body.message})


@router.post("/{po_number}/reply", response_model=DecisionOut)
def reply(po_number: str, body: Annotated[MessageIn, Body()], user: CanReply) -> DecisionOut:
    return _run(user, "reply_to_approval", {"po": po_number, "message": body.message})


@router.post("/{po_number}/decide", response_model=DecisionOut)
def decide(po_number: str, body: Annotated[DecideIn, Body()], owner: CanDecide) -> DecisionOut:
    return _run(owner, "decide_approval",
                {"po": po_number, "decision": body.decision, "note": body.note})  # fmt: skip
