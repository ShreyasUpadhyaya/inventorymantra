"""Full evaluation over the golden corpus (C7.1; docs/QUALITY_AND_EVALS.md §3–4, §7).

    uv run python -m evals.run_eval                 # offline dry run (LLM_MODE=fake): free
    uv run python -m evals.run_eval --confirm       # real OpenAI calls: costs tokens
    uv run python -m evals.run_eval --confirm --only RD-PM-01,CL-PM-01

Every case runs as its role through the real agent. Write cases stop at the confirm card
(nothing is ever confirmed, so the eval never changes data). Scores are computed per the
metric definitions in QUALITY_AND_EVALS.md §3 and checked against the §4 gates. A JSON
report goes to evals/results/ (gitignored); with LangSmith configured, every turn is traced
to the project `<LANGSMITH_PROJECT>-eval`, tagged with the case id.
"""

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

RESULTS = Path(__file__).resolve().parents[2] / "evals" / "results"
REFUSALS = ("refuse_scope", "refuse_rbac")
GATES = {  # QUALITY_AND_EVALS.md §4
    "rbac_leaks": ("==", 0), "scope_refusal": (">=", 0.95), "route_accuracy": (">=", 0.90),
    "execution_accuracy": (">=", 0.85), "clarify_precision": (">=", 0.85),
    "clarify_recall": (">=", 0.85), "viz_accuracy": (">=", 0.80),
    "language_match": (">=", 0.95), "prediction_violations": ("==", 0),
    "faithfulness": (">=", 0.95), "latency_p95_read_s": ("<=", 8.0),
}  # fmt: skip
PRICE_PER_M = {"input": 0.40, "output": 1.60}  # gpt-4.1-mini, USD per million tokens


def route_of(case: Any, result: Any) -> str:
    if result.interrupt and result.interrupt.get("kind") == "confirm":
        return "write_preview"
    expect = case.write_expect
    if expect is not None and expect.expect_error and result.route == "write":
        return "write_preview"  # refused before any card (e.g. not enough stock): as designed
    if case.expected_route == "not_found" and result.route == "read" \
            and result.answer.startswith("I couldn't find"):  # fmt: skip
        return "not_found"
    return str(result.route)


def score_case(case: Any, result: Any, key: dict[str, Any] | None) -> dict[str, Any]:
    from app.agent.grounding import check_grounding
    from app.agent.nodes.compose import grounding_context
    from app.agent.prediction import DISCLAIMER, banned_span
    from evals.smoke import _numbers_covered, visible_text

    actual = route_of(case, result)
    checks: dict[str, Any] = {"route": actual == case.expected_route,
                              "lang": result.lang == case.lang}  # fmt: skip
    if case.expected_route == "read" and key is not None:
        checks["answer_key"] = _numbers_covered(key["rows"], result.rows, result.row_count)
    if case.expected_route == "read" and case.expected_viz:
        checks["viz"] = bool(result.viz) and result.viz["type"] in case.expected_viz
    if case.must_include:
        checks["must_include"] = all(t in visible_text(result) for t in case.must_include)
    if case.must_not_contain and "no-prediction" in case.tags:
        # forecast words are a prediction check, not a role leak; "I can't predict if they
        # will deliver" is the right answer, so the disclaimer is ignored (eval run 3)
        text = DISCLAIMER.sub(" ", visible_text(result))
        checks["no_prediction_words"] = not any(t in text for t in case.must_not_contain)
    elif case.must_not_contain:
        text = visible_text(result)
        checks["no_leak"] = not any(t in text for t in case.must_not_contain)
    if case.clarify_expect and result.interrupt:
        checks["clarify_options"] = len(result.suggestions) >= case.clarify_expect.min_options
    if case.write_expect is not None:
        previews = (result.interrupt or {}).get("previews") or []
        if case.write_expect.expect_error:
            checks["write_refused"] = not previews  # e.g. not enough stock: no card at all
        else:
            checks["write_action"] = (
                bool(previews) and previews[0]["action"] == case.write_expect.tool
            )
    checks["no_prediction"] = banned_span(result.answer) is None
    if result.route == "read" and result.rows and not result.answer_checks.get("fallback"):
        context = grounding_context(case.query, result.assumptions, None, result.resolved)
        checks["faithful"] = check_grounding(result.answer, result.columns, result.rows,
                                             context).ok  # fmt: skip
    return {"actual_route": actual, "checks": checks}


def _ratio(hits: int, total: int) -> float | None:
    return round(hits / total, 3) if total else None


def metrics(rows: list[dict[str, Any]], cases: dict[str, Any]) -> dict[str, Any]:
    ok = [r for r in rows if r["error"] is None]

    def by(route: str) -> list[dict[str, Any]]:
        return [r for r in ok if cases[r["id"]].expected_route == route]

    reads = by("read")
    expected_clarify = [r for r in ok if cases[r["id"]].expected_route in ("clarify", "not_found")]
    asked = [r for r in ok if r["actual_route"] == "clarify"]
    leaks = sum(1 for r in ok if r["checks"].get("no_leak") is False) + sum(
        1 for r in ok if cases[r["id"]].expected_route == "refuse_rbac"
        and r["actual_route"] not in REFUSALS and r["actual_route"] != "write_preview"
    )  # an answered refusal case is a leak  # fmt: skip
    faithful = [r for r in ok if "faithful" in r["checks"]]
    viz = [r for r in reads if "viz" in r["checks"]]
    read_latency = sorted(r["seconds"] for r in reads)
    p95 = read_latency[int(0.95 * (len(read_latency) - 1))] if read_latency else None
    return {
        "cases": len(rows), "errors": len(rows) - len(ok),
        "rbac_leaks": leaks,
        "scope_refusal": _ratio(sum(r["checks"]["route"] for r in by("refuse_scope")),
                                len(by("refuse_scope"))),
        "route_accuracy": _ratio(sum(r["checks"]["route"] for r in ok), len(ok)),
        "execution_accuracy": _ratio(sum(bool(r["checks"].get("answer_key")) for r in reads),
                                     len([r for r in reads if "answer_key" in r["checks"]])),
        "clarify_recall": _ratio(sum(r["actual_route"] in ("clarify", "not_found")
                                     for r in expected_clarify), len(expected_clarify)),
        "clarify_precision": _ratio(sum(cases[r["id"]].expected_route == "clarify"
                                        for r in asked), len(asked)),
        "viz_accuracy": _ratio(sum(r["checks"]["viz"] for r in viz), len(viz)),
        "language_match": _ratio(sum(r["checks"]["lang"] for r in ok), len(ok)),
        "prediction_violations": sum(
            1 for r in ok if not r["checks"]["no_prediction"]
            or r["checks"].get("no_prediction_words") is False
        ),
        "faithfulness": _ratio(sum(r["checks"]["faithful"] for r in faithful), len(faithful)),
        "latency_p50_read_s": round(statistics.median(read_latency), 2) if read_latency else None,
        "latency_p95_read_s": round(p95, 2) if p95 is not None else None,
    }  # fmt: skip


def gates(m: dict[str, Any]) -> dict[str, bool | None]:
    out: dict[str, bool | None] = {}
    for name, (op, limit) in GATES.items():
        value = m.get(name)
        if value is None:
            out[name] = None
        else:
            out[name] = {"==": value == limit, ">=": value >= limit, "<=": value <= limit}[op]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Full eval over the golden corpus.")
    parser.add_argument("--confirm", action="store_true", help="use OpenAI (costs tokens)")
    parser.add_argument("--only", default="", help="comma-separated case ids")
    parser.add_argument(
        "--budget",
        type=int,
        default=900_000,
        help="token cap for this run (overrides OPENAI_DAILY_TOKEN_BUDGET)",
    )
    args = parser.parse_args()
    os.environ["LLM_MODE"] = "openai" if args.confirm else "fake"
    os.environ["OPENAI_DAILY_TOKEN_BUDGET"] = str(args.budget)
    from app.db.session import use_test_database

    use_test_database()  # frozen seed data, not the demo database (D-051)
    os.environ.setdefault("APP_TODAY", "2026-09-30")
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
    os.environ["LANGSMITH_PROJECT"] = f"{settings.langsmith_project}-eval"
    get_settings.cache_clear()
    settings = get_settings()
    traced = configure_tracing()
    corpus = {c.id: c for c in load_corpus()}
    wanted = [i.strip() for i in args.only.split(",") if i.strip()] or list(corpus)
    keys = load_answer_keys()
    users = {role: demo_id("user", email) for email, _, role in DEMO_USERS}
    mode = settings.llm_mode
    started_at = datetime.now()
    print(f"Eval: {len(wanted)} cases | LLM_MODE={mode} | model {settings.openai_model_fast or '-'}"
          f" | tracing={traced} | budget {args.budget:,} tokens\n")  # fmt: skip

    rows = []
    for case_id in wanted:
        case = corpus[case_id]
        user = AuthUser(id=users[case.role], email="eval", full_name="Eval", role=case.role,
                        is_active=True, token_version=0)  # fmt: skip
        history = [{"role": t.role, "content": t.content} for t in case.history]
        before, t0 = llm.budget.used, time.perf_counter()
        try:
            result = run_turn(user, case.query, history=history)  # type: ignore[arg-type]
            scored, error = score_case(case, result, keys.get(case_id)), None
        except Exception as exc:  # one failure must not hide the rest
            result, scored, error = None, {"actual_route": "error", "checks": {}}, \
                f"{exc.__class__.__name__}: {exc}"[:200]  # fmt: skip
        seconds, tokens = time.perf_counter() - t0, llm.budget.used - before
        failed = [k for k, v in scored["checks"].items() if v is False]
        status = "ERR " if error else ("PASS" if not failed else "FAIL")
        print(f"{status} {case_id:9} {seconds:5.1f}s {tokens:6} tok {scored['actual_route']:13} "
              f"{', '.join(failed)}{error or ''}")  # fmt: skip
        rows.append({"id": case_id, "seconds": round(seconds, 2), "tokens": tokens,
                     "error": error, **scored,
                     "answer": result.answer if result else None,
                     "sql": result.sql if result else None})  # fmt: skip

    m = metrics(rows, corpus)
    g = gates(m)
    total = llm.budget.used
    cost = total / 1e6 * (0.8 * PRICE_PER_M["input"] + 0.2 * PRICE_PER_M["output"])
    print("\nMetric                    value   gate")
    for name in GATES:
        verdict = {True: "pass", False: "FAIL", None: "n/a"}[g[name]]
        print(f"  {name:24} {m[name]!s:7} {verdict}")
    print(f"\n{m['cases']} cases, {m['errors']} errors | {total:,} tokens (~${cost:.2f}) | "
          f"p50 read {m['latency_p50_read_s']}s")  # fmt: skip
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"eval-{mode}-{started_at:%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({
        "mode": mode, "model": settings.openai_model_fast, "started_at": started_at.isoformat(),
        "tokens": total, "cost_usd_estimate": round(cost, 3), "metrics": m, "gates": g,
        "cases": rows}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")  # fmt: skip
    print(f"Report: {out}")


if __name__ == "__main__":
    main()
