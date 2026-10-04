"""Owner actions (WRITE_PATH.md §4): decide a PO waiting for approval, add a user,
deactivate a user. Same framework as every other write: preview, confirm, one transaction,
audit. The Approvals page and the chat both call these."""

from typing import Any, Literal
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from app.agent.formatting import inr
from app.auth.service import AuthUser
from app.services import users as users_service
from app.services.writes import resolve
from app.services.writes.framework import (
    Action,
    Ambiguous,
    NotFound,
    Outcome,
    Plan,
    WriteError,
    register,
)
from app.services.writes.stock import fmt_qty

ROLE_LABELS = {
    "inventory_manager": "Inventory Manager",
    "procurement_manager": "Procurement Manager",
    "owner": "Owner",
}


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ------------------------------------------------------------------ decide_approval
class DecideArgs(_Args):
    po: str = Field(min_length=1, max_length=40)
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=300)


APPROVAL_STATE = """SELECT a.id::text, a.status, p.status FROM approvals a
                    JOIN proc_purchase_orders p ON p.id = a.entity_id
                    WHERE a.id = %s"""
APPROVAL_LOCKED = APPROVAL_STATE + " FOR UPDATE OF a, p"


class DecideApproval(Action):
    name = "decide_approval"
    entity_type = "approval"
    args_model = DecideArgs

    def plan(self, conn: psycopg.Connection, user: AuthUser, args: DecideArgs) -> Plan:
        po = resolve.purchase_order(conn, user.role, args.po)
        row = conn.execute(
            """SELECT a.id::text, a.requested_by, u.full_name, v.name,
                      p.expected_delivery_date::text
               FROM approvals a JOIN users u ON u.id = a.requested_by
               JOIN proc_purchase_orders p ON p.id = a.entity_id
               LEFT JOIN proc_vendors v ON v.id = p.vendor_id
               WHERE a.entity_type = 'purchase_order' AND a.entity_id = %s
                 AND a.status = 'pending'""",
            (po.id,),
        ).fetchone()
        if row is None or po.status != "pending_approval":
            raise WriteError(f"{po.number} isn't waiting for your approval.")
        approval_id, requester_id, requester, vendor, _ = row
        lines = conn.execute(
            """SELECT i.name, l.quantity_ordered::float, coalesce(i.unit, l.unit),
                      l.line_total::float
               FROM proc_po_lines l JOIN inv_items i ON i.id = l.inv_item_id
               WHERE l.po_id = %s ORDER BY l.position""",
            (po.id,),
        ).fetchall()
        requests = conn.execute(
            "SELECT count(*) FROM stock_requests WHERE po_id = %s AND status = 'open'", (po.id,)
        ).fetchone()
        approve = args.decision == "approve"
        verb = "Approve" if approve else "Reject"
        fields = [("PO", po.number), ("Vendor", vendor or "—"), ("Total", inr(po.total)),
                  ("Requested by", requester)]  # fmt: skip
        fields += [(f"Line {i}", f"{n}: {fmt_qty(q, u)} = {inr(t)}")
                   for i, (n, q, u, t) in enumerate(lines, 1)]  # fmt: skip
        if args.note:
            fields.append(("Note", args.note))
        effects = [f"{po.number} is placed with the vendor." if approve
                   else f"{po.number} is rejected and won't be placed.",
                   f"{requester} is told the decision."]  # fmt: skip
        if requests and requests[0]:
            effects.append("The linked stock request is marked as ordered." if approve
                           else "The linked stock request goes back to open.")  # fmt: skip
        payload = {
            "approval_id": approval_id, "po_id": po.id, "po_number": po.number,
            "total": po.total, "vendor_name": vendor, "decision": args.decision,
            "note": args.note, "requested_by": str(requester_id), "requester_name": requester,
        }  # fmt: skip
        return Plan(payload=payload, summary=f"{verb} {po.number} for {inr(po.total)}.",
                    fields=fields, effects=effects)  # fmt: skip

    def read_state(
        self, conn: psycopg.Connection, payload: dict[str, Any], lock: bool
    ) -> dict[str, Any]:
        row = conn.execute(APPROVAL_LOCKED if lock else APPROVAL_STATE,
                           (payload["approval_id"],)).fetchone()  # fmt: skip
        return {"approval": list(row) if row else None}

    def apply(
        self, conn: psycopg.Connection, user: AuthUser, payload: dict[str, Any],
        state: dict[str, Any],
    ) -> Outcome:  # fmt: skip
        approve = payload["decision"] == "approve"
        status = "approved" if approve else "rejected"
        conn.execute(
            """UPDATE approvals SET status = %s, decided_by = %s, decided_at = now(), note = %s
               WHERE id = %s""",
            (status, user.id, payload["note"], payload["approval_id"]),
        )
        conn.execute(
            "UPDATE proc_purchase_orders SET status = %s, updated_at = localtimestamp "
            "WHERE id = %s",
            ("placed" if approve else "rejected", payload["po_id"]),
        )
        linked = conn.execute(
            """SELECT r.id::text, r.requested_by::text, i.name, r.quantity::float,
                      coalesce(i.unit, 'pcs')
               FROM stock_requests r JOIN inv_items i ON i.id = r.item_id
               WHERE r.po_id = %s AND r.status = 'open'""",
            (payload["po_id"],),
        ).fetchall()
        if approve:
            conn.execute("UPDATE stock_requests SET status = 'ordered', updated_at = now() "
                         "WHERE po_id = %s AND status = 'open'", (payload["po_id"],))  # fmt: skip
        else:
            conn.execute("UPDATE stock_requests SET po_id = NULL, updated_at = now() "
                         "WHERE po_id = %s AND status = 'open'", (payload["po_id"],))  # fmt: skip
        expected = conn.execute(
            """SELECT (SELECT max(d::date) FROM json_array_elements_text(
                         CASE WHEN json_typeof(expected_delivery_date) = 'array'
                              THEN expected_delivery_date END) d)::text
               FROM proc_purchase_orders WHERE id = %s""",
            (payload["po_id"],),
        ).fetchone()
        when = expected[0] if expected else None
        decided = {
            "po_number": payload["po_number"], "status": status, "note": payload["note"],
            "decided_by_name": user.full_name, "vendor_name": payload["vendor_name"],
            "total_amount": payload["total"], "expected_delivery_date": when,
        }  # fmt: skip
        events: list[dict[str, Any]] = [
            {
                "event": "approval_decided",
                "data": decided,
                "to": {"users": [payload["requested_by"]]},
            }
        ]
        for _, requester, item_name, qty, unit in linked:
            item_data = {**decided, "item_name": item_name, "quantity": qty, "unit": unit}
            events.append({"event": "approval_decided", "data": item_data,
                           "to": {"users": [requester]}})  # fmt: skip
        word = "approved and placed" if approve else "rejected"
        return Outcome(
            message=f"Done: {payload['po_number']} ({inr(payload['total'])}) is {word}. "
            f"{payload['requester_name']} has been told.",
            entity_id=payload["approval_id"], after={"status": status}, events=events,
        )  # fmt: skip


# ------------------------------------------------------------------ users
class CreateUserArgs(_Args):
    full_name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    role: Literal["inventory_manager", "procurement_manager", "owner"]


class CreateUser(Action):
    """The temporary password is NOT created here: it would pass through the chat history,
    the graph checkpoint and the trace. The Owner issues it on the Users page (Reset)."""

    name = "create_user"
    entity_type = "user"
    args_model = CreateUserArgs

    def plan(self, conn: psycopg.Connection, user: AuthUser, args: CreateUserArgs) -> Plan:
        try:
            email = users_service.check_new(conn, args.email, args.full_name, args.role)
        except users_service.UserError as exc:
            raise WriteError(exc.message) from None
        label = ROLE_LABELS[args.role]
        return Plan(
            payload={"email": email, "full_name": args.full_name, "role": args.role},
            summary=f"Add {args.full_name} ({email}) as {label}.",
            fields=[("Name", args.full_name), ("Email", email), ("Role", label)],
            effects=["They can sign in after you give them a temporary password from the "
                     "Users page (Reset password); they choose their own at first sign-in."],
        )  # fmt: skip

    def read_state(
        self, conn: psycopg.Connection, payload: dict[str, Any], lock: bool
    ) -> dict[str, Any]:
        row = conn.execute("SELECT count(*) FROM users WHERE email = %s",
                           (payload["email"],)).fetchone()  # fmt: skip
        return {"same_email": row[0] if row else 0}

    def apply(
        self, conn: psycopg.Connection, user: AuthUser, payload: dict[str, Any],
        state: dict[str, Any],
    ) -> Outcome:  # fmt: skip
        try:
            user_id, _unused = users_service.create(
                conn, user, payload["email"], payload["full_name"], payload["role"]
            )
        except users_service.UserError as exc:
            raise WriteError(exc.message) from None
        return Outcome(
            message=f"Done: {payload['full_name']} has an account as "
            f"{ROLE_LABELS[payload['role']]}. Open Users and press Reset password to give them "
            "a one-time temporary password.",
            entity_id=str(user_id), after={"email": payload["email"], "role": payload["role"]},
        )  # fmt: skip


class DeactivateArgs(_Args):
    user: str = Field(min_length=2, max_length=254)


def find_user(conn: psycopg.Connection, text: str) -> tuple[UUID, str, str, bool]:
    """By exact email, else by name (one match), never by a guess."""
    rows = conn.execute(
        """SELECT id, full_name, email, is_active FROM users
           WHERE lower(email) = lower(%s) OR full_name ILIKE %s ORDER BY full_name LIMIT 5""",
        (text.strip(), f"%{text.strip()}%"),
    ).fetchall()
    exact = [r for r in rows if r[2].lower() == text.strip().lower()]
    if exact:
        return exact[0]
    if len(rows) == 1:
        return rows[0]
    if rows:
        raise Ambiguous(f"“{text}” matches more than one person. Who do you mean?",
                        [f"{r[1]} ({r[2]})" for r in rows], field="user",
                        values=[r[2] for r in rows])  # fmt: skip
    raise NotFound(f"I couldn't find a user matching “{text}”.", field="user")


class DeactivateUser(Action):
    name = "deactivate_user"
    entity_type = "user"
    args_model = DeactivateArgs

    def plan(self, conn: psycopg.Connection, user: AuthUser, args: DeactivateArgs) -> Plan:
        user_id, name, email, active = find_user(conn, args.user)
        if user_id == user.id:
            raise WriteError("You can't deactivate your own account.")
        if not active:
            raise WriteError(f"{name} is already deactivated.")
        return Plan(
            payload={"user_id": str(user_id), "name": name, "email": email},
            summary=f"Deactivate {name} ({email}).",
            fields=[("Name", name), ("Email", email)],
            effects=["They are signed out everywhere at once and can't sign in again until "
                     "reactivated on the Users page."],
        )  # fmt: skip

    def read_state(
        self, conn: psycopg.Connection, payload: dict[str, Any], lock: bool
    ) -> dict[str, Any]:
        row = conn.execute("SELECT is_active FROM users WHERE id = %s",
                           (payload["user_id"],)).fetchone()  # fmt: skip
        return {"active": row[0] if row else None}

    def apply(
        self, conn: psycopg.Connection, user: AuthUser, payload: dict[str, Any],
        state: dict[str, Any],
    ) -> Outcome:  # fmt: skip
        try:
            users_service.set_active(conn, user, UUID(payload["user_id"]), False)
        except users_service.UserError as exc:
            raise WriteError(exc.message) from None
        return Outcome(message=f"Done: {payload['name']} is deactivated and signed out.",
                       entity_id=payload["user_id"], after={"is_active": False})  # fmt: skip


for _action in (DecideApproval(), CreateUser(), DeactivateUser()):
    register(_action)
