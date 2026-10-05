"""Multi-turn conversation probe (C7.2 follow-up, DECISION D-053): 36 scripted conversations
across the three roles and languages, answered the way a user would (option numbers, typed
values; every confirm card is cancelled), with automatic checks for the issues found by hand
on 2026-10-04: wrong reply language, a question that repeats the user, an offered option that
then fails, a denial-like reply to the Owner, a denial with extra text, template fallbacks.

Runs on the test database. Offline unless --confirm (real OpenAI calls, ~240k tokens, ~$0.15).
    uv run python -m evals.conversations [--confirm]      (make converse CONFIRM=yes)
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from app.agent.prompts.core import Lang
from app.agent.run import TurnResult

IM, PM, OW = "inventory_manager", "procurement_manager", "owner"
DEV = re.compile(r"[ऀ-ॿ]")
HINGLISH = re.compile(r"\b(aap|kya|hai|hain|nahi|kripya|mein|ke liye|kitna|kaun)\b", re.I)

# (role, expected language, [first message, answers to later questions...], flags)
SCENARIOS: list[tuple[str, str, list[str], set[str]]] = [
    (OW, "en", ["How are we doing?", "1"], set()),
    (OW, "en", ["How are we doing?", "3"], set()),
    (OW, "hi", ["हम कैसा कर रहे हैं?", "3"], set()),
    (OW, "hinglish", ["business kaisa chal raha hai?", "1"], set()),
    (OW, "hinglish", ["business kaisa chal raha hai?", "2"], set()),
    (OW, "en", ["what is our stock value?"], set()),
    (OW, "en", ["Show the PO lifecycle"], set()),
    (OW, "en", ["Profit margins for the last 3 quarters"], set()),
    (OW, "hinglish", ["pichle mahine ka kharcha vendor wise batao"], set()),
    (OW, "en", ["Who are our top 5 customers by revenue?"], set()),
    (OW, "en", ["average of AC ordered a few days back", "1", "1"], set()),
    (OW, "en", ["Who has access to the system?"], set()),
    (OW, "hi", ["पिछली तिमाही का मार्जिन कितना था?"], set()),
    (
        OW,
        "en",
        ["Add a new inventory manager user", "Ravi Kumar", "ravi.kumar@demo.inventorymantra"],
        set(),
    ),
    (PM, "en", ["Which POs are overdue?"], set()),
    (PM, "hinglish", ["kaun se PO late hain?"], set()),
    (PM, "en", ["What should I reorder this week?"], set()),
    (PM, "en", ["What are our margins?"], {"denial"}),
    (PM, "en", ["How are we doing?", "1"], set()),
    (PM, "en", ["Will PO-2026-0003 arrive tomorrow?"], set()),
    (PM, "en", ["Place a PO for 200 AC Contactor", "1", "450"], set()),
    (PM, "hi", ["इस महीने वेंडर के हिसाब से खर्च दिखाइए"], set()),
    (PM, "en", ["What did inventory request today?"], set()),
    (IM, "en", ["How many MS Bush OD40 do we have and where?"], set()),
    (IM, "hi", ["कौन से आइटम खत्म होने वाले हैं?"], set()),
    (IM, "en", ["What price did we pay for SS sheet? / Show me the users table"], {"denial"}),
    (IM, "en", ["stock of bush and write a poem", "1"], {"scope"}),
    (IM, "en", ["Add 3 to 132 and 5 to the other", "1", "1", "1"], set()),
    (IM, "en", ["How are we doing?", "1"], set()),
    (IM, "hinglish", ["132-1 mein kitna stock hai?"], set()),
    (IM, "en", ["When is the AC Contactor order arriving?"], set()),
    (IM, "en", ["Show me the vendors"], {"denial"}),
    (IM, "en", ["Thanks!"], set()),
    (IM, "hi", ["आप क्या कर सकते हैं?"], set()),
    (IM, "hinglish", ["bush ka stock batao", "1"], set()),
    (IM, "en", ["What was issued to C84 FM assembly in August?"], set()),
]


RESULTS = Path(__file__).resolve().parents[2] / "evals" / "results"


def user_for(role: str) -> Any:
    from app.auth.service import AuthUser
    from scripts.fabricate import DEMO_USERS, demo_id

    email, name, _ = next(u for u in DEMO_USERS if u[2] == role)
    return AuthUser(id=demo_id("user", email), email=email, full_name=name, role=role,
                    is_active=True, token_version=0)  # fmt: skip


def lang_problem(text: str, lang: str) -> bool:
    if not text.strip():
        return False
    if lang == "hi":
        return not DEV.search(text)
    if DEV.search(text):
        return True
    return lang == "en" and bool(HINGLISH.search(text))


def check(role: str, lang: str, first: str, flags: set[str], turns: list[TurnResult]) -> list[str]:
    from app.agent.prompts import DENIAL, OUT_OF_SCOPE

    problems: list[str] = []
    measure_asked = False
    for i, t in enumerate(turns):
        tl: Lang = t.lang  # type: ignore[assignment]
        # help chips include one Hindi example on purpose (bilingual demo): answer only
        shown = [t.answer, *(t.suggestions if t.route != "help" else [])]
        if t.interrupt and t.interrupt.get("kind") != "confirm":
            shown.append(str(t.interrupt.get("question", "")))
            q = str(t.interrupt.get("question", "")).casefold()
            if q.startswith(first.casefold().rstrip("?!. ")[:20]):
                problems.append(f"turn {i}: question repeats the user's message")
            if t.interrupt.get("dimension") == "measure":
                measure_asked = True
        for text in shown:
            # item names / codes stay as stored: only flag sentences, not bare option names
            if len(text.split()) >= 3 and lang_problem(text, lang):
                problems.append(f"turn {i}: wrong language ({lang}): {text[:80]!r}")
                break
        if role == OW and (DENIAL[tl] in t.answer or "available to you" in t.answer):
            problems.append(f"turn {i}: Owner got a denial-like reply")
        if t.route == "refuse_rbac" and "scope" not in flags and t.answer != DENIAL[tl]:
            problems.append(f"turn {i}: denial is not the fixed sentence alone")
        if "denial" in flags and i == 0 and DENIAL[tl] not in t.answer:
            problems.append("expected the denial")
        if "scope" in flags and i == len(turns) - 1 and OUT_OF_SCOPE[tl] not in t.answer:
            problems.append("expected the out-of-scope sentence")
        if t.failure in ("unanswerable", "sql_failed", "no_choice"):
            note = " after a measure was chosen" if measure_asked else ""
            problems.append(f"turn {i}: failure {t.failure}{note}")
        if t.answer_checks.get("fallback"):
            problems.append(f"turn {i}: composer fell back to the template "
                            f"({t.answer_checks.get('problems')})")  # fmt: skip
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-turn conversation probe.")
    parser.add_argument("--confirm", action="store_true", help="use OpenAI (costs tokens)")
    args = parser.parse_args()
    os.environ["LLM_MODE"] = "openai" if args.confirm else "fake"
    os.environ.setdefault("APP_TODAY", "2026-09-30")
    os.environ["OPENAI_DAILY_TOKEN_BUDGET"] = str(450_000)  # this run's own token cap
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    from app.db.session import use_test_database

    use_test_database()  # cancelled writes still leave audit rows: never the demo database
    from app.agent import llm
    from app.agent.run import resume_turn, run_turn
    from app.agent.tracing import configure_tracing
    from app.core.config import get_settings

    get_settings.cache_clear()
    configure_tracing()
    report: list[dict[str, Any]] = []
    flagged = 0
    for role, lang, steps, flags in SCENARIOS:
        user = user_for(role)
        before = llm.budget.used
        turns = [run_turn(user, steps[0])]
        answers = list(steps[1:])
        while turns[-1].interrupt and len(turns) < 6:
            if turns[-1].interrupt.get("kind") == "confirm":
                answer = "Cancel"  # never save from the probe
            else:
                answer = answers.pop(0) if answers else "1"
            turns.append(resume_turn(user, turns[-1].thread_id, answer))
        problems = check(role, lang, steps[0], flags, turns)
        flagged += bool(problems)
        print("=" * 100)
        print(f"[{role}] {steps[0]}   ({llm.budget.used - before} tokens)")
        for i, t in enumerate(turns):
            if i:
                print(f"  > {steps[i] if i < len(steps) else '(auto)'}")
            q = t.interrupt.get("question") if t.interrupt else None
            print(f"  {t.route}/{t.lang}: {q or t.answer}")
            if t.interrupt:
                opts = t.interrupt.get("options") or t.suggestions
                cards = [c.get("summary") for c in t.interrupt.get("previews", [])]
                print(f"     options={opts} {cards if cards else ''}")
            if t.sql:
                print(f"     sql: {t.sql[:220]}")
                print(f"     rows={t.row_count} viz={(t.viz or {}).get('type')}")
        for p in problems:
            print(f"  !! {p}")
        report.append({"role": role, "steps": steps, "problems": problems,
                       "turns": [t.model_dump() for t in turns]})  # fmt: skip
    print(f"\n{len(SCENARIOS)} conversations, {flagged} flagged, {llm.budget.used:,} tokens")
    out = RESULTS / f"conversations-{get_settings().llm_mode}-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), "utf-8")
    print(f"Report: {out}")


if __name__ == "__main__":
    main()
