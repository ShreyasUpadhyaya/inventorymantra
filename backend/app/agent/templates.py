"""Template answers (the fallback when the model's answer fails a check, and the whole answer
offline): conversational sentences built straight from the rows, so they are grounded by
construction. Columns we know are phrased as sentences; the rest read "label is value" (D-040).
"""

from typing import Any

from app.agent.formatting import label, show
from app.agent.prompts.core import Lang
from app.agent.prompts.replies import FIRST_ONE, FOUND_ROWS, NO_ROWS, PREDICT_NOTE
from app.agent.signals import FUTURE_QUESTION

SUBJECTS = ("po_number", "item_name", "vendor_name")
AMOUNTS = ("total_amount", "amount", "po_total")


def _days(lang: Lang, n: Any) -> str:
    plural = "" if n == 1 else "s"
    return {"en": f"{n} day{plural}", "hi": f"{n} दिन", "hinglish": f"{n} din"}[lang]


def _join(lang: Lang, parts: list[str]) -> str:
    and_ = {"en": "and", "hi": "और", "hinglish": "aur"}[lang]
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + f" {and_} " + parts[-1]


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _end(text: str) -> str:
    return text if text.endswith((".", "।")) else text + "."


def _fact(lang: Lang, column: str, value: Any) -> str:
    verb = " is " if lang == "en" else " "
    return f"{label(column)}{verb}{show(column, value)}"


def describe(lang: Lang, columns: list[str], row: list[Any]) -> str:
    """One record as plain sentences."""
    cells = dict(zip(columns, row, strict=True))
    subject = next((c for c in SUBJECTS if cells.get(c) is not None), None)
    if subject is None:
        return _capital(_join(lang, [_fact(lang, c, cells[c]) for c in columns])) + "."

    used = {subject}
    name = str(cells[subject])
    vendor = cells.get("vendor_name") if subject != "vendor_name" else None
    if vendor:
        used.add("vendor_name")
    head = name + (
        f" from {vendor}" if vendor and lang == "en" else f" ({vendor})" if vendor else ""
    )

    bits: list[str] = []
    if cells.get("status") is not None:
        used.add("status")
        status = cells["status"]
        bits.append({"en": f"is {status}", "hi": f"अभी {status} है",
                     "hinglish": f"abhi {status} hai"}[lang])  # fmt: skip
    amount_col = next((c for c in AMOUNTS if cells.get(c) is not None), None)
    if amount_col:
        used.add(amount_col)
        amount = show(amount_col, cells[amount_col])
        bits.append({"en": f"worth {amount}", "hi": f"कुल राशि {amount}",
                     "hinglish": f"total amount {amount}"}[lang])  # fmt: skip

    date = overdue = None
    if "expected_delivery_date" in cells:
        used.add("expected_delivery_date")
        date = show("expected_delivery_date", cells["expected_delivery_date"])
    if cells.get("days_overdue") is not None:
        used.add("days_overdue")
        overdue = _days(lang, cells["days_overdue"])
    others = [_fact(lang, c, cells[c]) for c in columns if c not in used]

    if not bits:  # "PO-2026-0009 from Granite Steels: overdue by 53 days."
        clauses = []
        if date:
            clauses.append({"en": f"expected delivery date is {date}",
                            "hi": f"अपेक्षित डिलीवरी तिथि {date} है",
                            "hinglish": f"expected delivery date {date} hai"}[lang])  # fmt: skip
        if overdue:
            clauses.append({"en": f"overdue by {overdue}", "hi": f"{overdue} लेट है",
                            "hinglish": f"{overdue} late hai"}[lang])  # fmt: skip
        clauses += others
        return head + ": " + _join(lang, clauses) + "." if clauses else _end(head)

    sentences = [head + " " + _join(lang, bits) + "."]
    if date:
        due = {"en": f"Its expected delivery date is {date}.",
               "hi": f"इसकी अपेक्षित डिलीवरी तिथि {date} है।",
               "hinglish": f"Iski expected delivery date {date} hai."}  # fmt: skip
        sentences.append(due[lang])
    if overdue:
        sentences.append({"en": f"It is {overdue} overdue.", "hi": f"यह {overdue} लेट है।",
                          "hinglish": f"Yeh {overdue} late hai."}[lang])  # fmt: skip
    if others:
        lead = {"en": "Other details: ", "hi": "अन्य जानकारी: ", "hinglish": "Aur jaankari: "}[lang]
        sentences.append(lead + _join(lang, others) + ".")
    return " ".join(sentences)


def template_answer(lang: Lang, columns: list[str], rows: list[list[Any]], message: str) -> str:
    if not rows:
        return NO_ROWS[lang]
    note = PREDICT_NOTE[lang] + " " if FUTURE_QUESTION.search(message) else ""
    if len(rows) == 1:
        return note + describe(lang, columns, rows[0])
    first = describe(lang, columns, rows[0])
    return f"{note}{FOUND_ROWS[lang].format(n=len(rows))} {FIRST_ONE[lang]} {first}"
