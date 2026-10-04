"""Names in the user's words → database ids, for writes (WRITE_PATH.md §1: the server picks
ids, never the model). Uses the same matcher as reads (D-026): one clear match is used,
several close ones become a question, none is a polite "couldn't find"."""

import re
from dataclasses import dataclass

import psycopg

from app.agent import entity_match
from app.agent.entity_match import Kind
from app.services.writes.framework import Ambiguous, NotFound, WriteError


@dataclass(frozen=True)
class Item:
    id: str
    code: str
    name: str
    unit: str
    active: bool


@dataclass(frozen=True)
class Location:
    id: str
    name: str


@dataclass(frozen=True)
class Target:
    id: str
    name: str


WHAT = {"item": "an item", "location": "a warehouse", "vendor": "a vendor", "po": "a PO"}


def _match(conn: psycopg.Connection, role: str, kind: Kind, text: str, field: str) -> str:
    text = (text or "").strip()
    if not text:
        raise WriteError(f"Please say which {WHAT[kind].split(' ', 1)[1]}.", field=field)
    found = entity_match.resolve(conn, role, kind, text)
    if found.outcome == "match":
        return found.candidates[0].code
    names = [c.name for c in found.candidates]
    codes = [c.code for c in found.candidates]  # picking an option uses its exact code
    if found.outcome == "clarify":
        raise Ambiguous(f"“{text}” matches more than one record. Which one do you mean?",
                        names, field=field, values=codes)  # fmt: skip
    if found.outcome == "denied":
        raise WriteError("That information isn't available for your role.")
    raise NotFound(f"I couldn't find {WHAT[kind]} matching “{text}”.", names, field=field,
                   values=codes)  # fmt: skip


def item(conn: psycopg.Connection, role: str, text: str, field: str = "item") -> Item:
    code = _match(conn, role, "item", text, field)
    row = conn.execute(
        "SELECT id, item_code, name, coalesce(unit, 'pcs'), status = 'active' "
        "FROM inv_items WHERE item_code = %s",
        (code,),
    ).fetchone()
    if row is None:
        raise NotFound(f"I couldn't find an item matching “{text}”.")
    found = Item(*row)
    if not found.active:
        raise WriteError(f"{found.name} is marked inactive, so it can't be changed.")
    return found


def location(conn: psycopg.Connection, role: str, text: str, field: str = "location") -> Location:
    name = _match(conn, role, "location", text, field)  # active warehouses only
    row = conn.execute(
        "SELECT id, name FROM inv_locations WHERE name = %s AND is_active", (name,)
    ).fetchone()
    if row is None:
        raise NotFound(f"I couldn't find an active warehouse matching “{text}”.")
    return Location(*row)


def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def issue_target(conn: psycopg.Connection, text: str, field: str = "issued_to") -> Target:
    """Shop floors and teams stock is issued to (8 names): exact, then contains, then
    trigram similarity."""
    text = (text or "").strip()
    if not text:
        raise WriteError(
            "Please say who the stock is issued to (a shop floor or team).", field=field
        )
    rows = [
        Target(str(i), str(n))
        for i, n in conn.execute(
            "SELECT id, name FROM inv_issued_to_targets WHERE is_active ORDER BY name"
        ).fetchall()
    ]
    query = _plain(text)
    exact = [t for t in rows if _plain(t.name) == query]
    contains = [t for t in rows if query and query in _plain(t.name)]
    for pool in (exact, contains):
        if len(pool) == 1:
            return pool[0]
        if len(pool) > 1:
            raise Ambiguous(
                f"“{text}” matches more than one team. Which one do you mean?",
                [t.name for t in pool],
                field=field,
            )
    scored = conn.execute(
        """SELECT id, name FROM inv_issued_to_targets
           WHERE is_active AND similarity(lower(name), lower(%s)) >= 0.3
           ORDER BY similarity(lower(name), lower(%s)) DESC LIMIT 4""",
        (text, text),
    ).fetchall()
    if len(scored) == 1:
        return Target(str(scored[0][0]), str(scored[0][1]))
    if scored:
        raise Ambiguous(
            f"“{text}” could be more than one team. Which one do you mean?",
            [str(n) for _, n in scored],
            field=field,
        )
    raise NotFound(f"I couldn't find a shop floor or team matching “{text}”.",
                   [t.name for t in rows][:4], field=field)  # fmt: skip


@dataclass(frozen=True)
class Vendor:
    id: str
    name: str
    payment_terms: str | None


@dataclass(frozen=True)
class PurchaseOrder:
    id: str
    number: str
    status: str
    vendor_id: str | None
    total: float


def vendor(conn: psycopg.Connection, role: str, text: str, field: str = "vendor") -> Vendor:
    name = _match(conn, role, "vendor", text, field)  # active vendors only; denied for the IM
    row = conn.execute(
        "SELECT id, name, payment_terms FROM proc_vendors WHERE name = %s AND is_active", (name,)
    ).fetchone()
    if row is None:
        raise NotFound(f"I couldn't find an active vendor matching “{text}”.")
    return Vendor(*row)


def purchase_order(
    conn: psycopg.Connection, role: str, text: str, field: str = "po"
) -> PurchaseOrder:
    number = _match(conn, role, "po", text, field)
    row = conn.execute(
        """SELECT id, po_number, status, vendor_id, coalesce(total_amount, 0)::float
           FROM proc_purchase_orders WHERE po_number = %s""",
        (number,),
    ).fetchone()
    if row is None:
        raise NotFound(f"I couldn't find a PO matching “{text}”.")
    return PurchaseOrder(*row)
