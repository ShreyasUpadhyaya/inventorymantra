"""Answer composer (C4.4): prose from the rows, a chart, and two checks before anything
reaches the user (DECISION D-010):
  grounding  - every number must be in the rows or derivable from them (code)
  prediction - no statements about the future (banned phrases in code; judge model only
               when future-ish wording appears)
A failing answer is regenerated once; if it fails again the user gets a template answer
built straight from the rows, which is grounded by construction.
"""

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from app.agent import fake
from app.agent.formatting import label, show
from app.agent.grounding import check_grounding
from app.agent.llm import LLMBudgetExceededError, LLMConfigError, text_call
from app.agent.nodes.guard import neutralise, system_prompt
from app.agent.nodes.read import effective_message
from app.agent.prediction import check_prediction
from app.agent.prompts import DENIAL, Lang, render
from app.agent.prompts.compose import COMPOSE_TASK
from app.agent.prompts.replies import FOUND_ROWS, NO_ROWS
from app.agent.state import AgentState
from app.agent.viz import choose_viz
from app.core.config import get_settings

logger = logging.getLogger("inventorymantra.agent")

LANGUAGE_NAMES = {
    "en": "English",
    "hi": "Hindi (Devanagari script)",
    "hinglish": "Hinglish (Hindi written in Roman script)",
}
ROWS_FOR_MODEL = 50  # bigger results: the first 50 rows plus whole-result totals
RESULT: dict[Lang, str] = {"en": "Result", "hi": "परिणाम", "hinglish": "Result"}
FOR_RECORD: dict[Lang, str] = {
    "en": "For {name}: ",
    "hi": "{name} के लिए: ",
    "hinglish": "{name} ke liye: ",
}


def name_matched_records(answer: str, state: AgentState) -> str:
    """D-026: always name what a fuzzy mention was matched to, so a wrong match is visible."""
    missing = [r["name"] for r in state.get("resolved", []) if r["name"] not in answer]
    if not missing:
        return answer
    return FOR_RECORD[state["lang"]].format(name=", ".join(missing)) + answer


def _payload(columns: list[str], rows: list[list[Any]]) -> str:
    if len(rows) <= ROWS_FOR_MODEL:  # the count is stated so the model never has to count
        return json.dumps({"row_count": len(rows), "rows": rows}, ensure_ascii=False, default=str)
    totals = {
        c: round(sum(r[i] for r in rows if isinstance(r[i], int | float)), 2)
        for i, c in enumerate(columns)
        if any(isinstance(r[i], int | float) and not isinstance(r[i], bool) for r in rows)
    }
    return json.dumps(
        {"row_count": len(rows), "first_rows": rows[:ROWS_FOR_MODEL], "column_totals": totals},
        ensure_ascii=False, default=str,
    )  # fmt: skip


def compose_messages(role: str, state: AgentState) -> list[Any]:
    task = render(COMPOSE_TASK, language=LANGUAGE_NAMES[state["lang"]])
    columns = state.get("columns", [])
    user = (
        f"<user_message>{neutralise(effective_message(state))}</user_message>"
        f"<assumptions>{neutralise('; '.join(state.get('assumptions', [])))}</assumptions>"
    )
    if state.get("denied_areas"):
        user += f"<denied_parts>{', '.join(state['denied_areas'])}</denied_parts>"
    if state.get("resolved"):  # the records the question was matched to (from the DB)
        matched = json.dumps(state["resolved"], ensure_ascii=False)
        user += f"<data>{neutralise(matched)}</data>"
    user += (
        f'<data columns="{", ".join(columns)}">'
        f"{neutralise(_payload(columns, state.get('rows', [])))}</data>"
    )
    return [SystemMessage(system_prompt(role, task)), HumanMessage(user)]


def template_answer(state: AgentState) -> str:
    """Built straight from the rows: always grounded, never predictive."""
    lang = state["lang"]
    columns, rows = state.get("columns", []), state.get("rows", [])
    if not rows:
        return NO_ROWS[lang]
    if len(rows) == 1:
        parts = [f"{label(c)}: {show(c, v)}" for c, v in zip(columns, rows[0], strict=True)]
        return f"{RESULT[lang]} — " + "; ".join(parts) + "."
    first = "; ".join(
        f"{label(c)}: {show(c, v)}" for c, v in list(zip(columns, rows[0], strict=True))[:3]
    )
    return f"{FOUND_ROWS[lang].format(n=len(rows))} ({first} …)"


def _context(state: AgentState) -> list[str]:
    """Text whose numbers the answer may repeat: the question, assumptions, chosen records."""
    texts = [state["message"], *state.get("assumptions", [])]
    texts += [c["answer"] for c in state.get("clarifications", [])]
    texts += [f"{r['code']} {r['name']}" for r in state.get("resolved", [])]  # may be repeated
    return texts


def _check(answer: str, state: AgentState) -> list[str]:
    problems = []
    grounding = check_grounding(
        answer, state.get("columns", []), state.get("rows", []), _context(state)
    )
    if not grounding.ok:
        problems.append(f"ungrounded: {', '.join(grounding.unsupported)}")
    predictive = check_prediction(answer)
    if predictive:
        problems.append(f"predictive: {predictive}")
    return problems


def compose(state: AgentState, config: RunnableConfig) -> AgentState:
    role = config["configurable"]["role"]
    viz = choose_viz(
        effective_message(state), state.get("columns", []), state.get("rows", []),
        state["guard"].explicit_viz,
    )  # fmt: skip
    answer, problems, fallback = "", [], False
    if get_settings().llm_mode == "fake" and not fake.is_scripted("compose"):
        answer, fallback = template_answer(state), True  # offline: the template is the answer
    else:
        for temperature in (0.0, 0.2):  # one regeneration, slightly varied
            try:
                candidate = text_call(
                    "compose", "reasoning", compose_messages(role, state), temperature
                )
                problems = _check(candidate, state)
            except (LLMBudgetExceededError, LLMConfigError):
                problems = ["model unavailable"]
                break
            if not problems:
                answer = candidate
                break
            logger.warning("composer answer rejected: %s", "; ".join(problems))
        if not answer:
            answer, fallback = template_answer(state), True
    answer = name_matched_records(answer, state)
    if state.get("denied_areas") and DENIAL[state["lang"]] not in answer:
        answer = f"{answer} {DENIAL[state['lang']]}"
    return {"answer": answer, "suggestions": [], "viz": viz.model_dump(),
            "answer_checks": {"fallback": fallback, "problems": problems}}  # fmt: skip
