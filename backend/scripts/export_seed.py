"""One-off: turn the raw `database_export/` CSVs into sanitised seeds under `data/seed/`.

Only business tables are copied. Users, refresh tokens, the old audit log and LangGraph
checkpoints are excluded (hashes, emails, 21 MB of chat state). JSON columns that the raw
export wrote as Python reprs (``['2026-08-13']``) are normalised to real JSON.

Run from backend/:  uv run python -m scripts.export_seed
"""

import ast
import csv
import json
import sys
from pathlib import Path

from scripts.seed import SEED_DIR, SEED_TABLES

RAW_DIR = Path(__file__).resolve().parents[2] / "database_export"
REPR_JSON_COLUMNS = {"expected_delivery_date", "expected_dispatch_date"}


def normalise(column: str, value: str) -> str:
    if column in REPR_JSON_COLUMNS and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = ast.literal_eval(value)
        return json.dumps(parsed)
    return value


def main() -> None:
    if not RAW_DIR.exists():
        sys.exit(f"{RAW_DIR} not found; nothing to export.")
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    for table in SEED_TABLES:
        src = RAW_DIR / f"public.{table}.csv"
        with src.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            fields = list(reader.fieldnames or [])
            rows = [{c: normalise(c, v) for c, v in row.items()} for row in reader]
        with (SEED_DIR / f"{table}.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        print(f"{table}: {len(rows)} rows")


if __name__ == "__main__":
    main()
