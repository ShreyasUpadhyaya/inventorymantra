"""Compute and freeze answer keys for the golden corpus (QUALITY_AND_EVALS.md §2).

Each read case's SQL runs **as that case's role** (its read-only DB user, its view schema,
its demo user id) with the business date frozen at EVAL_TODAY. So a key can only use data
the role may see; a key that needs a forbidden view fails here, not silently in evals.

Run from backend/:  uv run python -m evals.build_answer_keys   (or `make answer-keys`)
"""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.db.readonly import readonly_session
from app.db.session import use_test_database
from evals.schema import ANSWER_KEYS_PATH, EvalCase, load_corpus
from scripts.fabricate import DEMO_USERS, demo_id

EVAL_TODAY = date(2026, 9, 30)
DEMO_USER_IDS: dict[str, UUID] = {role: demo_id("user", email) for email, _, role in DEMO_USERS}


def to_json(value: Any) -> Any:
    if isinstance(value, Decimal):  # plain notation, no trailing zeros: 10879200, 20.9
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def compute_key(case: EvalCase) -> dict[str, Any]:
    assert case.answer_key_sql is not None
    with readonly_session(case.role, DEMO_USER_IDS[case.role], EVAL_TODAY) as conn:
        cursor = conn.execute(case.answer_key_sql)  # corpus SQL is reviewed, trusted input
        columns = [d.name for d in cursor.description or []]
        rows = [[to_json(v) for v in row] for row in cursor.fetchall()]
    if case.compare != "exact":
        # Order only matters for "exact"; otherwise freeze a canonical order, so ties in
        # ORDER BY (e.g. equal days_overdue) can't make keys differ between machines.
        rows.sort(key=lambda r: json.dumps(r, ensure_ascii=False))
    if case.expect_empty != (len(rows) == 0):
        expectation = "empty" if case.expect_empty else "non-empty"
        raise ValueError(f"{case.id}: expected a {expectation} result, got {len(rows)} rows")
    missing = set(case.key_columns) - set(columns)
    if missing:
        raise ValueError(f"{case.id}: key_columns not in result: {sorted(missing)}")
    return {"columns": columns, "rows": rows, "row_count": len(rows)}


def build_all() -> dict[str, dict[str, Any]]:
    return {case.id: compute_key(case) for case in load_corpus() if case.answer_key_sql}


def main() -> None:
    use_test_database()  # keys come from the freshly seeded test database (D-051)
    keys = build_all()
    ANSWER_KEYS_PATH.write_text(
        json.dumps(keys, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Wrote {len(keys)} answer keys to {ANSWER_KEYS_PATH.name} (as of {EVAL_TODAY}).")


if __name__ == "__main__":
    main()
