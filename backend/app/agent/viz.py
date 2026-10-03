"""Choose the visual for an answer by rules (DECISION D-008); no model call.

Order: explicit request (if the data can carry it) -> process/lifecycle question -> shape:
  no rows -> none; one row -> kpi; time axis + numbers -> line; share/split question with
  <= 6 parts -> pie; category + numbers with <= 12 rows -> bar; anything else -> table.
"""

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel

from app.agent.signals import FIGURE_QUESTION

VizType = Literal["none", "kpi", "table", "bar", "line", "pie", "stacked_bar", "flow"]

FLOW_WORDS = re.compile(r"\b(lifecycle|life cycle|process|workflow|flow|stages|steps)\b", re.I)
SHARE_WORDS = re.compile(
    r"\b(split|share|breakdown|distribution|proportion|percentage|mix)\b|हिस्सा|बंटवारा", re.I
)
LIST_WORDS = re.compile(r"\b(show|list|lines|details|records|rows)\b|दिखाइए|dikhao", re.I)
TIME_COLUMNS = re.compile(
    r"^(month|quarter|week|year|date|day)$|_(date|on|month|at)$|^quarter", re.I
)
QUARTER = re.compile(r"^\d{4}-Q[1-4]$")

# Purchase-order status flow (WRITE_PATH.md §3), drawn with live counts.
PO_FLOW = [
    ("pending_approval", "placed"), ("pending_approval", "rejected"), ("placed", "partial"),
    ("partial", "received"), ("placed", "received"), ("placed", "cancelled"),
]  # fmt: skip
PO_LABELS = {
    "pending_approval": "Pending approval", "placed": "Placed", "partial": "Partly received",
    "received": "Received", "rejected": "Rejected", "cancelled": "Cancelled",
}  # fmt: skip


class VizSpec(BaseModel):
    type: VizType
    title: str
    x: str | None = None
    y: list[str] = []
    mermaid: str | None = None


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_time(column: str, values: list[Any]) -> bool:
    if TIME_COLUMNS.search(column):
        return True
    sample = [v for v in values if v is not None][:5]
    if not sample:
        return False
    if all(isinstance(v, str) and QUARTER.match(v) for v in sample):
        return True
    try:
        return all(isinstance(v, str) and date.fromisoformat(v[:10]) for v in sample)
    except ValueError:
        return False


def _title(question: str) -> str:
    text = question.strip().rstrip("?")
    return text[:80] if len(text) <= 80 else text[:77] + "..."


def po_flow_mermaid(columns: list[str], rows: list[list[Any]]) -> str:
    counts: dict[str, Any] = {}
    if "status" in columns:
        s = columns.index("status")
        n = next(
            (i for i, c in enumerate(columns) if c != "status" and rows and _is_number(rows[0][i])),
            None,
        )
        counts = {r[s]: (r[n] if n is not None else "") for r in rows}

    def node(status: str) -> str:
        count = counts.get(status, 0)
        return f'{status}["{PO_LABELS[status]} ({count})"]'

    lines = ["flowchart LR"] + [f"  {node(a)} --> {node(b)}" for a, b in PO_FLOW]
    return "\n".join(lines)


def choose_viz(
    question: str, columns: list[str], rows: list[list[Any]], explicit: str | None = None
) -> VizSpec:
    title = _title(question)
    if not rows or not columns:
        return VizSpec(type="none", title=title)
    numeric = [
        c for i, c in enumerate(columns)
        if all(_is_number(r[i]) or r[i] is None for r in rows)
        and any(_is_number(r[i]) for r in rows)
    ]  # fmt: skip
    labels = [c for c in columns if c not in numeric]
    time_axis = next((c for c in columns if _is_time(c, [r[columns.index(c)] for r in rows])), None)
    x = time_axis or (labels[0] if labels else None)
    y = [c for c in numeric if c != x]
    chartable = x is not None and bool(y)

    if FLOW_WORDS.search(question):
        return VizSpec(type="flow", title=title, mermaid=po_flow_mermaid(columns, rows))
    if explicit == "table":
        return VizSpec(type="table", title=title)
    if explicit == "kpi" and len(rows) == 1:
        return VizSpec(type="kpi", title=title, y=numeric)
    if explicit in ("bar", "line", "pie") and chartable:
        return VizSpec(type=explicit, title=title, x=x, y=y[:1] if explicit == "pie" else y)
    if len(rows) == 1:
        # One record. Cards only for a figure ("how many", "total") or a row of pure metrics;
        # a lookup is described in the sentence and its details stay under "Show data" (D-040).
        if LIST_WORDS.search(question) and len(columns) > 2:
            return VizSpec(type="table", title=title)
        if FIGURE_QUESTION.search(question) or not labels:
            return VizSpec(type="kpi", title=title, y=numeric)
        return VizSpec(type="none", title=title)
    text_columns = [c for c in labels if c != time_axis]
    if len(text_columns) >= 2 or (time_axis and text_columns) or len(rows) > 12:
        return VizSpec(type="table", title=title)  # a detail list, not a series
    if chartable and time_axis:
        return VizSpec(type="line", title=title, x=x, y=y)
    if chartable and SHARE_WORDS.search(question) and len(rows) <= 6 and len(y) == 1:
        return VizSpec(type="pie", title=title, x=x, y=y)
    if chartable:
        return VizSpec(type="bar", title=title, x=x, y=y)
    return VizSpec(type="table", title=title)
