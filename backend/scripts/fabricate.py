"""Deterministic demo data (seed=42, as of 2026-09-30). See DECISION D-012 / D-024.

Creates: 3 demo users (one per role), reorder policies for regularly used items, demo
sales for Finished Goods (Oct 2025 - Sep 2026) and a few stock requests from the IM.
Every fabricated row is labelled source='demo'. Called by scripts.seed after the CSV load.
"""

import math
import random
import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import psycopg

from app.auth.passwords import hash_password
from app.core.config import TIMEZONE, get_settings

SEED = 42
DATA_AS_OF = date(2026, 9, 30)
USAGE_WINDOW_DAYS = 90
MIN_ACTIVE_WEEKS = 2  # "regularly used" = issued in at least 2 distinct weeks in the window
NAMESPACE = uuid.UUID("6f1c2a52-0d1e-4c55-9d7a-3b8f4d2e9a10")

DEMO_USERS = [
    ("owner@demo.inventorymantra", "Demo Owner", "owner"),
    ("procurement@demo.inventorymantra", "Demo Procurement Manager", "procurement_manager"),
    ("inventory@demo.inventorymantra", "Demo Inventory Manager", "inventory_manager"),
]

# Lead-time ranges (days) by sub-category short code; anything else uses DEFAULT_LEAD.
LEAD_TIME_BY_SUBCATEGORY = {
    "MT": (15, 30),  # raw material: metal
    "NM": (15, 30),  # raw material: non-metal
    "MC": (10, 25),  # bought out: mechanical
    "EL": (21, 45),  # bought out: electrical
    "CN": (7, 14),  # bought out: consumable
    "SA": (20, 40),  # bought out: sub-assembly
    "DE": (20, 40),  # sub-assembly: dock equipment
}
DEFAULT_LEAD = (14, 30)

CUSTOMERS = [  # fictional
    "Northline Metro Corporation",
    "Coastal Rail Transit Ltd.",
    "Deccan Urban Transit Authority",
    "Ganga Metro Services",
    "Western Rail Systems Pvt. Ltd.",
    "Sahyadri Infra Projects",
]
# Selling price = cost x markup; markup 1.18-1.45 => margin ~15-31% (user-approved range).
# Each quarter gets its own band inside that range so the Owner's margin trend isn't flat.
MARKUP_BY_QUARTER = {
    (2025, 4): (1.22, 1.40),
    (2026, 1): (1.25, 1.45),
    (2026, 2): (1.18, 1.34),
    (2026, 3): (1.24, 1.42),
}
# Scaled so quarterly sales (~Rs 1 cr) sit plausibly next to procurement spend (~Rs 85 L/qtr).
UNIT_COST_RANGE = (20_000, 250_000)
QTY_RANGE = (1, 5)


@dataclass(frozen=True)
class Summary:
    users: int
    reorder_policies: int
    sales_orders: int
    sales_lines: int
    stock_requests: int
    generated_password: str | None


def demo_id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, "/".join(parts))


def at(d: date, hour: int = 10) -> datetime:
    return datetime(d.year, d.month, d.day, hour, tzinfo=TIMEZONE)


def nice_round(x: float) -> int:
    step = 1 if x <= 20 else 5 if x <= 100 else 10 if x <= 1000 else 50
    return max(step, int(math.ceil(x / step) * step))


def clear(conn: psycopg.Connection) -> None:
    conn.execute(
        "TRUNCATE notifications, approvals, stock_requests, reorder_policy, sales_order_lines,"
        " sales_orders, refresh_tokens, agent_audit_log, users CASCADE"
    )


def create_users(conn: psycopg.Connection) -> tuple[dict[str, uuid.UUID], str | None]:
    configured = get_settings().demo_password
    generated = None if configured else secrets.token_urlsafe(9)
    password = configured.get_secret_value() if configured else generated
    assert password is not None
    password_hash = hash_password(password)  # one hash is fine: same password for all demo users
    ids: dict[str, uuid.UUID] = {}
    owner_id = demo_id("user", DEMO_USERS[0][0])
    for email, name, role in DEMO_USERS:
        user_id = demo_id("user", email)
        ids[role] = user_id
        conn.execute(
            """INSERT INTO users (id, email, password_hash, full_name, role, created_by)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (user_id, email, password_hash, name, role, None if role == "owner" else owner_id),
        )
    return ids, generated


def create_reorder_policies(conn: psycopg.Connection, rng: random.Random) -> int:
    window_start = DATA_AS_OF - timedelta(days=USAGE_WINDOW_DAYS)
    rows = conn.execute(
        """
        WITH usage AS (
            SELECT item_id, sum(-quantity) AS used,
                   count(DISTINCT date_trunc('week', created_at)) AS active_weeks
            FROM inv_transactions
            WHERE transaction_type = 'outbound' AND created_at >= %s AND created_at < %s
            GROUP BY item_id
        ),
        last_vendor AS (
            SELECT DISTINCT ON (l.inv_item_id) l.inv_item_id AS item_id, p.vendor_id
            FROM proc_po_lines l JOIN proc_purchase_orders p ON p.id = l.po_id
            ORDER BY l.inv_item_id, p.placed_on DESC, p.po_number DESC
        )
        SELECT u.item_id, u.used, sc.short_code, lv.vendor_id
        FROM usage u
        JOIN inv_items i ON i.id = u.item_id AND i.status = 'active'
        LEFT JOIN inv_sub_categories sc ON sc.id = i.sub_category_id
        LEFT JOIN last_vendor lv ON lv.item_id = u.item_id
        WHERE u.active_weeks >= %s
        ORDER BY u.item_id
        """,
        (window_start, DATA_AS_OF + timedelta(days=1), MIN_ACTIVE_WEEKS),
    ).fetchall()
    for item_id, used, short_code, vendor_id in rows:
        daily = float(used) / USAGE_WINDOW_DAYS
        low, high = LEAD_TIME_BY_SUBCATEGORY.get(short_code or "", DEFAULT_LEAD)
        conn.execute(
            """INSERT INTO reorder_policy (id, item_id, min_level, reorder_qty, lead_time_days,
                   safety_stock, preferred_vendor_id, source, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'demo', %s)""",
            (
                demo_id("reorder", item_id),
                item_id,
                nice_round(daily * 30),
                nice_round(daily * 60),
                rng.randint(low, high),
                math.ceil(daily * 7),
                vendor_id,
                at(DATA_AS_OF, 9),
            ),
        )
    return len(rows)


def create_sales(conn: psycopg.Connection, rng: random.Random) -> tuple[int, int]:
    items = [
        r[0]
        for r in conn.execute(
            """SELECT i.id FROM inv_items i JOIN inv_categories c ON c.id = i.category_id
               WHERE c.short_code = 'FG' AND i.status = 'active' ORDER BY i.id"""
        ).fetchall()
    ]
    unit_cost = {item: rng.randrange(*UNIT_COST_RANGE, 500) for item in items}
    orders = lines = 0
    month = date(2025, 10, 1)
    while month <= DATA_AS_OF:
        next_month = (month + timedelta(days=32)).replace(day=1)
        last_day = min(next_month - timedelta(days=1), DATA_AS_OF)
        for n in range(1, rng.randint(3, 7) + 1):
            order_date = month + timedelta(days=rng.randint(0, (last_day - month).days))
            if order_date > DATA_AS_OF - timedelta(days=10):
                status = "open"
            else:
                status = "cancelled" if rng.random() < 0.03 else "delivered"
            so_number = f"SO-{order_date:%y%m}-{n:02d}"
            order_id = demo_id("so", so_number)
            conn.execute(
                """INSERT INTO sales_orders (id, so_number, customer_name, order_date, status)
                   VALUES (%s, %s, %s, %s, %s)""",
                (order_id, so_number, rng.choice(CUSTOMERS), order_date, status),
            )
            orders += 1
            markup = MARKUP_BY_QUARTER[(order_date.year, (order_date.month - 1) // 3 + 1)]
            for line_no, item in enumerate(rng.sample(items, rng.randint(1, 3)), start=1):
                cost = unit_cost[item]
                price = round(cost * rng.uniform(*markup), -2)
                conn.execute(
                    """INSERT INTO sales_order_lines (id, sales_order_id, item_id, quantity,
                           unit_price, unit_cost) VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        demo_id("sol", so_number, str(line_no)),
                        order_id,
                        item,
                        rng.randint(*QTY_RANGE),
                        price,
                        cost,
                    ),
                )
                lines += 1
        month = next_month
    return orders, lines


def create_stock_requests(
    conn: psycopg.Connection, rng: random.Random, users: dict[str, uuid.UUID]
) -> int:
    """Open requests for the four items with the least cover, plus one fulfilled request."""
    rows = conn.execute(
        """
        SELECT r.item_id, r.reorder_qty
        FROM reorder_policy r
        LEFT JOIN (SELECT item_id, sum(quantity) q FROM inv_current_stock GROUP BY item_id) s
               ON s.item_id = r.item_id
        ORDER BY coalesce(s.q, 0) / NULLIF(r.min_level, 0) NULLS LAST, r.item_id
        LIMIT 5
        """
    ).fetchall()
    for i, (item_id, qty) in enumerate(rows):
        fulfilled = i == len(rows) - 1
        created = DATA_AS_OF - timedelta(
            days=rng.randint(12, 20) if fulfilled else rng.randint(0, 3)
        )
        conn.execute(
            """INSERT INTO stock_requests (id, item_id, quantity, needed_by, note, status,
                   requested_by, handled_by, source, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'demo', %s, %s)""",
            (
                demo_id("request", item_id),
                item_id,
                qty,
                created + timedelta(days=rng.randint(7, 21)),
                "Running low on the shop floor" if not fulfilled else "Needed for dispatch batch",
                "fulfilled" if fulfilled else "open",
                users["inventory_manager"],
                users["procurement_manager"] if fulfilled else None,
                at(created, 11),
                at(created + timedelta(days=5 if fulfilled else 0), 12),
            ),
        )
    return len(rows)


def fabricate(conn: psycopg.Connection) -> Summary:
    rng = random.Random(SEED)  # noqa: S311 (deterministic demo data, not crypto)
    clear(conn)
    users, generated = create_users(conn)
    policies = create_reorder_policies(conn, rng)
    orders, lines = create_sales(conn, rng)
    requests = create_stock_requests(conn, rng, users)
    return Summary(len(users), policies, orders, lines, requests, generated)
