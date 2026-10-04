"""Smoke test: a handful of golden-corpus questions through the real agent (CLAUDE.md §4a).

    uv run python -m evals.smoke              # offline dry run (LLM_MODE=fake): free
    uv run python -m evals.smoke --confirm    # real OpenAI calls: costs tokens

Each case is scored on route, answer key (rows), required/forbidden text, chart type and
language, and the run reports token use and latency. A JSON report goes to evals/results/
(gitignored). Traces go to LangSmith when tracing is configured.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

SMOKE_IDS = [
    "RD-PM-01",  # overdue POs: SQL + table
    "RD-OW-01",  # margins for 3 quarters: chart + ₹ prose
    "RD-IM-13",  # Hindi count
    "RD-PM-10",  # Hindi figure (user request)
    "ER-IM-09",  # Hinglish + item matching
    "CL-PM-01",  # vague period: asks back
    "RB-IM-01",  # IM asks prices: fixed denial
    "RB-IM-17",  # forbidden + off-topic: both sentences
    "RB-PM-09",  # PM claims to be the Owner: denial (user request)
    "NP-PM-01",  # no prediction
    "RD-PM-03",  # status split: pie
    "HL-IM-04",  # prompt extraction attempt
]
RESULTS = Path(__file__).resolve().parents[2] / "evals" / "results"


def _comparable(rows: list[list[Any]]) -> list[list[Any]]:
    from evals.build_answer_keys import to_json

    def norm(value: Any) -> Any:
        value = to_json(value)
        if isinstance(value, bool) or value is None:
            return value
        try:
            return round(float(value), 4)
        except (TypeError, ValueError):
            return value

    return sorted(([norm(v) for v in r] for r in rows), key=lambda r: json.dumps(r, default=str))


def _numbers(rows: list[list[Any]]) -> list[float]:
    found = []
    for row in _comparable(rows):
        found += [v for v in row if isinstance(v, float)]
    return found


def _numbers_covered(
    key_rows: list[list[Any]], result_rows: list[list[Any]], row_count: int | None = None
) -> bool:
    """Every number in the answer key appears in the result (any column order or extra
    columns; a SUM over one row equals the row). Dates and names are not compared. A key that
    is one count is also met by that many result rows ("how many items…" listed, C7.2)."""
    key_numbers = _numbers(key_rows)
    if len(key_rows) == 1 and len(key_numbers) == 1 and row_count == key_numbers[0]:
        return True
    pool = _numbers(result_rows)
    for number in key_numbers:
        if number not in pool:
            return False
        pool.remove(number)
    return True


def visible_text(result: Any) -> str:
    """What the user can see: the answer, buttons, table and any confirm card. (Internal
    fields such as ids or the names of denied areas are not shown, so they can't leak.)"""
    shown = {"answer": result.answer, "suggestions": result.suggestions,
             "columns": result.columns, "rows": result.rows,
             "previews": (result.interrupt or {}).get("previews")}  # fmt: skip
    return json.dumps(shown, ensure_ascii=False, default=str)


def score(case: Any, result: Any, key: dict[str, Any] | None) -> dict[str, Any]:
    expected = case.expected_route
    if expected == "not_found":
        route_ok = result.route == "read" and result.answer.startswith("I couldn't find")
    else:
        route_ok = result.route == expected
    checks: dict[str, Any] = {"route": route_ok, "lang": result.lang == case.lang}
    if key is not None and expected == "read":
        checks["answer_key"] = _numbers_covered(key["rows"], result.rows, result.row_count)
    if case.expected_viz and expected == "read":
        checks["viz"] = bool(result.viz) and result.viz["type"] in case.expected_viz
    if case.must_include:
        checks["must_include"] = all(t in visible_text(result) for t in case.must_include)
    if case.must_not_contain:
        text = visible_text(result)
        checks["no_leak"] = not any(t in text for t in case.must_not_contain)
    if case.clarify_expect and result.interrupt:
        checks["clarify_options"] = len(result.suggestions) >= case.clarify_expect.min_options
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test over a few golden cases.")
    parser.add_argument("--confirm", action="store_true", help="use OpenAI (costs tokens)")
    args = parser.parse_args()
    os.environ["LLM_MODE"] = "openai" if args.confirm else "fake"
    os.environ.setdefault("APP_TODAY", "2026-09-30")
    from app.db.session import use_test_database

    use_test_database()  # frozen seed data, not the demo database (D-051)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    from app.agent import llm
    from app.agent.run import run_turn
    from app.agent.tracing import configure_tracing
    from app.auth.service import AuthUser
    from app.core.config import get_settings
    from evals.schema import load_answer_keys, load_corpus
    from scripts.fabricate import DEMO_USERS, demo_id

    get_settings.cache_clear()
    settings = get_settings()
    traced = configure_tracing()
    corpus = {c.id: c for c in load_corpus()}
    keys = load_answer_keys()
    users = {role: demo_id("user", email) for email, _, role in DEMO_USERS}
    mode = settings.llm_mode
    print(f"Smoke test: {len(SMOKE_IDS)} cases | LLM_MODE={mode} | model "
          f"{settings.openai_model_fast or '-'} | tracing={traced}\n")  # fmt: skip

    report, passed = [], 0
    for case_id in SMOKE_IDS:
        case = corpus[case_id]
        user = AuthUser(id=users[case.role], email="smoke", full_name="Smoke", role=case.role,
                        is_active=True, token_version=0)  # fmt: skip
        before, started = llm.budget.used, time.perf_counter()
        try:
            result = run_turn(user, case.query)
            checks, error = score(case, result, keys.get(case_id)), None
        except Exception as exc:  # report and continue: one failure must not hide the rest
            result, checks, error = None, {"ran": False}, f"{exc.__class__.__name__}: {exc}"[:200]
        seconds, tokens = time.perf_counter() - started, llm.budget.used - before
        ok = all(checks.values())
        passed += ok
        failed = [k for k, v in checks.items() if not v]
        print(f"{'PASS' if ok else 'FAIL'} {case_id:9} {seconds:5.1f}s {tokens:6} tok  "
              f"{(result.route if result else 'error'):12} {', '.join(failed)}")  # fmt: skip
        if result is not None:
            print(f"      Q: {case.query}\n      A: {result.answer[:300]}")
        report.append({"id": case_id, "ok": ok, "checks": checks, "error": error,
                       "seconds": round(seconds, 2), "tokens": tokens,
                       "result": result.model_dump() if result else None})  # fmt: skip

    total = llm.budget.used
    print(f"\n{passed}/{len(SMOKE_IDS)} passed | {total} tokens | mode={mode}")
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"smoke-{mode}-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"mode": mode, "model": settings.openai_model_fast,
                               "passed": passed, "total": len(SMOKE_IDS), "tokens": total,
                               "cases": report}, ensure_ascii=False, indent=1, default=str),
                   encoding="utf-8")  # fmt: skip
    print(f"Report: {out}")


if __name__ == "__main__":
    main()
