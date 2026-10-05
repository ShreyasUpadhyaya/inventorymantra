"""Grounding check (DECISION D-010): numbers in an answer must be supported by the data.

A number is supported if it equals (within rounding) a value in the result rows, a number
inside a text cell (codes, names, dates), a simple aggregate of a numeric column (sum,
average, min, max, count, a share of the column total), the row count, or a number the user
or the system context already stated (question, assumptions, clarifications).
Understands Indian grouping (₹47,89,288.83), lakh/crore/thousand words in English and Hindi,
percentages and Devanagari digits. Numbers glued to letters (OD40, M3) are names, not values.
"""

import re
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import date
from statistics import mean
from typing import Any

DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
MULTIPLIERS = {
    "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "लाख": 1e5,
    "crore": 1e7, "crores": 1e7, "cr": 1e7, "करोड़": 1e7, "करोड": 1e7,
    "thousand": 1e3, "k": 1e3, "हज़ार": 1e3, "हजार": 1e3,
}  # fmt: skip
_NUMBER = re.compile(
    r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)"
    r"(?:\s*(lakhs?|lacs?|crores?|cr|thousand|लाख|करोड़|करोड|हज़ार|हजार)|(k))?(?![\w])",
    re.I,
)


@dataclass(frozen=True)
class Grounding:
    ok: bool
    unsupported: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Found:
    text: str
    value: float
    scaled: bool  # had a lakh/crore/thousand word: displayed rounded
    in_date: bool = False  # part of a written date ("8 Aug 2026", "2026-08-08")


_MONTH = (
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*|"
    r"जनवरी|फ़रवरी|फरवरी|मार्च|अप्रैल|मई|जून|जुलाई|अगस्त|सितंबर|अक्टूबर|नवंबर|दिसंबर"
)
_DATE_SPANS = re.compile(
    rf"\d{{4}}-\d{{1,2}}-\d{{1,2}}|\d{{4}}-Q[1-4]|"
    rf"\b\d{{1,2}}(?:\s*[–-]\s*\d{{1,2}})?\s+(?:{_MONTH})(?:\s+\d{{4}})?|"
    rf"(?:{_MONTH})\s+\d{{1,2}}(?:,?\s+\d{{4}})?|(?:{_MONTH})\s+\d{{4}}|\b(?:19|20)\d{{2}}\b",
    re.I,
)


def _find(text: str) -> list[_Found]:
    text = text.translate(DEVANAGARI_DIGITS)
    date_spans = [m.span() for m in _DATE_SPANS.finditer(text)]
    out = []
    for m in _NUMBER.finditer(text):
        raw, word = m.group(1).rstrip(","), m.group(2) or m.group(3)
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if word:
            value *= MULTIPLIERS[word.lower()]
        in_date = any(start <= m.start(1) < end for start, end in date_spans)
        out.append(_Found(m.group(0).strip(), value, bool(word), in_date))
    return out


def extract_numbers(text: str) -> list[float]:
    return [f.value for f in _find(text)]


def _date_parts(value: Any) -> list[float]:
    if not isinstance(value, str):
        return []
    try:
        d = date.fromisoformat(value[:10])
    except ValueError:
        return (
            [float(n) for n in re.findall(r"\d+", value)]
            if re.match(r"^\d{4}-Q[1-4]$", value)
            else []
        )
    return [float(d.day), float(d.month), float(d.year)]


def _cell_numbers(value: Any) -> list[float]:
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, int | float):
        return [float(value)]
    text = str(value)
    if _date_parts(text):  # a date's parts may only back numbers written as dates
        return []
    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text.translate(DEVANAGARI_DIGITS))]
    with suppress(ValueError):
        numbers.append(float(text))  # numeric strings such as "4789288.83"
    return numbers


def _is_value(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _allowed(
    columns: list[str], rows: list[list[Any]], context: list[str]
) -> tuple[set[float], set[float]]:
    """(values any number may match, extra parts only a written date may match)."""
    allowed: set[float] = {0.0, 1.0, float(len(rows))}
    date_parts: set[float] = set()
    for row in rows:
        for value in row:
            allowed.update(_cell_numbers(value))
            date_parts.update(_date_parts(value))
    for index in range(len(columns)):
        numeric = [float(r[index]) for r in rows if _is_value(r[index])]
        if not numeric:
            continue
        total = sum(numeric)
        allowed.update({total, mean(numeric), min(numeric), max(numeric), float(len(numeric))})
        if total:
            allowed.update(100 * v / total for v in numeric)
    for text in context:
        allowed.update(extract_numbers(text))
        allowed.update(float(n) for n in re.findall(r"\d+(?:\.\d+)?", text))
    return allowed, date_parts


def _supported(found: _Found, allowed: set[float]) -> bool:
    for a in allowed:
        tolerance = max(0.01, abs(a) * (0.012 if found.scaled else 0.005))
        if abs(found.value - a) <= tolerance:
            return True
        for digits in (0, 1, 2):  # displayed rounded: 48.3 for 48.333, 51 for 50.986 (k)
            if round(a, digits) == found.value:
                return True
    return False


# "N purchase orders / items / records ..." is a count claim: N must be a real count.
COUNT_NOUNS = re.compile(
    r"(?<![\w.])(\d+(?:,\d+)*)\s+(?:(?!और\b|aur\b|and\b|or\b)\w+\s+)?"
    r"(purchase orders?|POs?|orders?|items?|records?|rows?|vendors?|"
    r"suppliers?|requests?|users?|customers?|deliveries|receipts?|instalments?|lines?|"
    r"आइटम|ऑर्डर|रिकॉर्ड|वेंडर)\b",
    re.I,
)


def _counts(columns: list[str], rows: list[list[Any]], context: list[str]) -> set[float]:
    """Numbers a count claim may use: the row count, distinct values per column, how many
    rows share each value, count-like cells, a single row's figures, numbers the user gave."""
    counts: set[float] = {float(len(rows))}
    for index in range(len(columns)):
        values = [r[index] for r in rows]
        counts.add(float(len({str(v) for v in values})))
        for value in set(map(str, values)):
            counts.add(float(sum(1 for v in values if str(v) == value)))
        if len(rows) == 1 or re.search(r"count|pos$|items|orders|number|^n$", columns[index]):
            counts.update(float(v) for v in values if _is_value(v))
    for text in context:
        counts.update(extract_numbers(text))
    return counts


def check_grounding(
    answer: str, columns: list[str], rows: list[list[Any]], context: list[str]
) -> Grounding:
    allowed, date_parts = _allowed(columns, rows, context)
    unsupported = [
        f.text
        for f in _find(answer)
        # a number written as part of a date (day, year) must match exactly: the rounding
        # tolerance for amounts would let 2031 pass for 2026
        if not (f.value in allowed | date_parts if f.in_date else _supported(f, allowed))
    ]
    counts = _counts(columns, rows, context)
    for claim in COUNT_NOUNS.finditer(answer.translate(DEVANAGARI_DIGITS)):
        number = float(claim.group(1).replace(",", ""))
        if number not in counts and claim.group(0) not in unsupported:
            unsupported.append(claim.group(0))  # smoke run 2: "31 purchase orders" for 28
    return Grounding(ok=not unsupported, unsupported=unsupported)
