"""Display formatting shared by template answers: ₹ with Indian grouping, dates like
13 Aug 2026, readable column labels."""

import re
from datetime import date
from typing import Any

MONEY_COLUMN = re.compile(
    r"(amount|total|spend|revenue|cost|price|value|profit|po_total)", re.I
)  # fmt: skip


def indian_group(integer: int) -> str:
    text = str(abs(integer))
    if len(text) <= 3:
        grouped = text
    else:
        head, tail = text[:-3], text[-3:]
        parts: list[str] = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        grouped = ",".join(parts) + "," + tail
    return f"-{grouped}" if integer < 0 else grouped


def inr(value: float) -> str:
    rounded = round(value, 2)
    whole = int(rounded)
    paise = round(abs(rounded - whole) * 100)
    return f"₹{indian_group(whole)}" + (f".{paise:02d}" if paise else "")


def number(value: float | int) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return indian_group(int(value))


def label(column: str) -> str:
    return column.replace("_", " ")


def show(column: str, value: Any) -> str:
    if value is None:
        return "not recorded"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int | float):
        if column.endswith("_pct"):
            return f"{number(value)}%"
        return inr(float(value)) if MONEY_COLUMN.search(column) else number(value)
    text = str(value)
    try:
        d = date.fromisoformat(text[:10])
        return f"{d.day} {d:%b %Y}"
    except ValueError:
        return text
