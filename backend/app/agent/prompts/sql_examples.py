"""Few-shot examples for the SQL prompt's {few_shots} slot, per role.

Rules (enforced by tests/agent/test_sql_examples.py):
- each example uses only that role's views, passes the SQL guard and runs as the role;
- no example repeats a golden-corpus question (keeps evals honest);
- examples assume "today is Wednesday 30 Sep 2026" and teach the date conventions of
  QUALITY_AND_EVALS.md §2 (calendar months, previous Mon-Sun week, last completed quarter).
"""

import json
from dataclasses import dataclass

from app.db.readonly import AppRole


@dataclass(frozen=True)
class Example:
    question: str
    sql: str | None
    assumptions: tuple[str, ...] = ()

    def render(self) -> str:
        answer = {"sql": self.sql, "assumptions": list(self.assumptions),
                  "answerable": self.sql is not None}  # fmt: skip
        return f"Q: {self.question}\nA: {json.dumps(answer, ensure_ascii=False)}"


EXAMPLES_IM = (
    Example(
        'How much Relay Base RXZE1M2C is in each warehouse?  <data>[{"type": "item", '
        '"code": "BO-EL-0140", "name": "Relay Base RXZE1M2C"}]</data>',
        "SELECT location_name, quantity, unit FROM stock_by_location "
        "WHERE item_code = 'BO-EL-0140' ORDER BY quantity DESC",
    ),
    Example(
        "Which items went to G133 Shop Floor in July?",
        "SELECT item_code, item_name, sum(-quantity) AS quantity_issued, unit "
        "FROM stock_movements WHERE issued_to = 'G133 Shop Floor' AND movement_type = 'outbound' "
        "AND created_at >= DATE '2026-07-01' AND created_at < DATE '2026-08-01' "
        "GROUP BY item_code, item_name, unit ORDER BY quantity_issued DESC",
        ("July = 1-31 Jul 2026",),
    ),
    Example(
        "How many raw material items are in stock?",
        "SELECT count(DISTINCT s.item_id) AS items_in_stock FROM stock_by_location s "
        "JOIN items i USING (item_id) WHERE i.category = 'Raw Material' AND s.quantity > 0",
    ),
    Example(
        "Which items have less than 10 days of stock left at current usage?",
        "SELECT item_code, item_name, on_hand, unit, avg_daily_use_90d, days_of_cover, "
        "order_by_date FROM reorder_status WHERE days_of_cover < 10 ORDER BY days_of_cover",
        ("days of cover = on hand / average daily use over the last 90 days",),
    ),
    Example(
        "What movements were recorded in the week before last? (today is Wed 30 Sep 2026)",
        "SELECT created_at::date AS movement_date, item_name, movement_type, quantity, unit, "
        "location_name FROM stock_movements WHERE created_at >= DATE '2026-09-14' "
        "AND created_at < DATE '2026-09-21' ORDER BY created_at",
        ("week before last = Mon 14 Sep - Sun 20 Sep 2026 (weeks run Monday-Sunday)",),
    ),
    Example("What is the weight of each item?", None, ("item weight is not recorded",)),
)

EXAMPLES_PM = (
    Example(
        "Total value of POs placed with Aster Steels in August",
        "SELECT sum(total_amount) AS total_value, count(*) AS pos FROM purchase_orders "
        "WHERE vendor_name = 'Aster Steels Pvt. Ltd.' AND placed_on >= DATE '2026-08-01' "
        "AND placed_on < DATE '2026-09-01'",
        ("August = 1-31 Aug 2026",),
    ),
    Example(
        'Average price we paid for SS Sheet 304 1250x2500x1.5mm  <data>[{"type": "item", '
        '"code": "RM-MT-FS-0057", "name": "SS Sheet – 304 – 1250×2500×1.5mm"}]</data>',
        "SELECT round(avg(unit_price), 2) AS avg_unit_price, count(*) AS po_lines "
        "FROM po_lines WHERE item_code = 'RM-MT-FS-0057'",
    ),
    Example(
        "Which POs are only partly received?",
        "SELECT po_number, vendor_name, total_amount, expected_delivery_date FROM purchase_orders "
        "WHERE status = 'partial' ORDER BY po_number",
    ),
    Example(
        "Which advance payments are still unpaid?",
        "SELECT po_number, vendor_name, stage, amount FROM payment_tranches "
        "WHERE is_advance AND NOT is_cleared ORDER BY po_number",
    ),
    Example(
        "Monthly spend with Sterling Steels",
        "SELECT month, total_spend FROM spend_by_vendor_month "
        "WHERE vendor_name = 'Sterling Steels Pvt. Ltd.' ORDER BY month",
    ),
    Example(
        "Which active vendors are in Pune?",
        "SELECT vendor_name, payment_terms FROM vendors WHERE city = 'Pune' AND is_active "
        "ORDER BY vendor_name",
    ),
)

EXAMPLES_OWNER_ONLY = (
    Example(
        "Revenue by customer in Q1 2026",
        "SELECT customer_name, sum(revenue) AS revenue FROM sales_orders "
        "WHERE order_date >= DATE '2026-01-01' AND order_date < DATE '2026-04-01' "
        "AND status <> 'cancelled' GROUP BY customer_name ORDER BY revenue DESC",
        ("Q1 2026 = 1 Jan - 31 Mar 2026; cancelled orders excluded",),
    ),
    Example(
        "Gross profit by month since January",
        "SELECT date_trunc('month', order_date)::date AS month, sum(gross_profit) AS gross_profit "
        "FROM sales_orders WHERE order_date >= DATE '2026-01-01' AND status <> 'cancelled' "
        "GROUP BY 1 ORDER BY 1",
    ),
    Example(
        "Which procurement managers are active?",
        "SELECT full_name, email FROM users WHERE role = 'procurement_manager' AND is_active "
        "ORDER BY full_name",
    ),
)

EXAMPLES: dict[AppRole, tuple[Example, ...]] = {
    "inventory_manager": EXAMPLES_IM,
    "procurement_manager": EXAMPLES_IM[:2] + EXAMPLES_PM,
    "owner": EXAMPLES_PM[:4] + EXAMPLES_OWNER_ONLY,
}


def few_shots(role: AppRole) -> str:
    return "\n\n".join(example.render() for example in EXAMPLES[role])
