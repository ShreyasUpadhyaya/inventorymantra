"""Clarification (C4.3): pause the graph with options, resume where it stopped.

LangGraph re-runs an interrupted node from its first line on resume, so every model or
database call that must not repeat lives in a separate node *before* the one that pauses:
  resolve_entities (DB)  -> ask_entity   (interrupt only)
  clarify_check  (model) -> ask_clarify  (interrupt only)
"""

import re
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict

from app.agent.entity_match import normalise, resolve
from app.agent.llm import LLMBudgetExceededError, LLMConfigError, structured_call
from app.agent.nodes.guard import neutralise, system_prompt
from app.agent.prompts.clarify import CLARIFY_TASK
from app.agent.prompts.replies import DIDNT_RECOGNISE, WHICH_ONE
from app.agent.signals import VAGUE_MEASURE, VAGUE_TIME
from app.agent.state import AgentState
from app.db.pool import get_pool

MAX_ENTITY_ASKS = 2  # after two unusable answers, stop asking and say not found


class ClarifyOutput(BaseModel):
    """PROMPTS.md §3 output. All fields required for OpenAI strict JSON schema."""

    model_config = ConfigDict(extra="forbid")

    needs_clarification: bool
    dimension: Literal["time_window", "entity", "measure"] | None
    question: str | None
    options: list[str]
    default_applied: str | None


# ------------------------------------------------------------------ entity: which one?
def ask_entity(state: AgentState, config: RunnableConfig) -> AgentState:
    issue = state["entity_issue"] or {}
    lang = state["lang"]
    question = WHICH_ONE[lang].format(text=issue["text"])
    if issue.get("unrecognised"):
        question = f"{DIDNT_RECOGNISE[lang].format(answer=issue['unrecognised'])} {question}"
    choice = str(
        interrupt(
            {
                "kind": "clarify",
                "dimension": "entity",
                "question": question,
                "options": issue["options"],
            }
        )  # fmt: skip
    ).strip()
    asks = state.get("entity_asks", 0) + 1
    picked = _pick(issue, choice)
    if picked is None:  # free text: maybe it names the record more precisely
        with get_pool().connection() as conn:
            result = resolve(conn, config["configurable"]["role"], issue["type"], choice)
        if result.outcome == "match":
            picked = (result.candidates[0].code, result.candidates[0].name)
        elif asks >= MAX_ENTITY_ASKS:  # stop asking; never guess a record
            return {"entity_issue": {**issue, "outcome": "no_choice"}, "entity_asks": asks}
        elif result.outcome == "clarify":  # a different set of close matches: offer those
            again = {
                **issue,
                "text": choice,
                "options": [c.name for c in result.candidates],
                "codes": [c.code for c in result.candidates],
                "unrecognised": None,
            }
            return {"entity_issue": again, "entity_asks": asks}
        else:  # unrecognised: ask the same question again, saying why
            return {"entity_issue": {**issue, "unrecognised": choice[:60]}, "entity_asks": asks}
    resolved = [*state.get("resolved", []),
                {"type": issue["type"], "text": issue["text"], "code": picked[0],
                 "name": picked[1]}]  # fmt: skip
    return {"resolved": resolved, "entity_issue": None, "entity_asks": asks,
            "mention_index": state.get("mention_index", 0) + 1}  # fmt: skip


def _pick(issue: dict[str, Any], choice: str) -> tuple[str, str] | None:
    """Accept an offered option by its text, its code, or its 1-based number."""
    options, codes = issue["options"], issue.get("codes", issue["options"])
    if choice.isdigit() and 1 <= int(choice) <= len(options):
        i = int(choice) - 1
        return codes[i], options[i]
    for code, name in zip(codes, options, strict=True):
        if choice.casefold() in (code.casefold(), name.casefold()):
            return code, name
    # A fragment that fits exactly one offered option ("OD40" -> "MS Bush – OD40×ID26.5×70mm").
    fragment = normalise(choice)
    hits = [
        (c, n) for c, n in zip(codes, options, strict=True) if fragment and fragment in normalise(n)
    ]
    return hits[0] if len(hits) == 1 else None


def after_ask_entity(
    state: AgentState,
) -> Literal["resolve_entities", "ask_entity", "respond_entity"]:
    issue = state.get("entity_issue")
    if issue is None:
        return "resolve_entities"
    return "ask_entity" if issue["outcome"] == "clarify" else "respond_entity"


# ------------------------------------------------------------------ period / measure
def clarify_messages(role: str, state: AgentState) -> list[Any]:
    import json

    data = json.dumps(state.get("resolved", []), ensure_ascii=False)
    user = f"<user_message>{neutralise(state['message'])}</user_message><data>{data}</data>"
    return [SystemMessage(system_prompt(role, CLARIFY_TASK)), HumanMessage(user)]


_PUNCT = "?.!,।'\"“”‘’"
_GAP = rf"[\s{re.escape(_PUNCT)}]"


def without_echo(question: str, message: str) -> str:
    """Drop the user's own words when the model repeats them before its question
    ("Business kaisa chal raha hai? Aap kis measure…", user test 2026-10-04)."""
    words = [w.strip(_PUNCT) for w in message.split() if w.strip(_PUNCT)]
    pattern = rf"{_GAP}*" + rf"{_GAP}+".join(map(re.escape, words)) + rf"{_GAP}*"
    start = re.match(pattern, question, re.I) if words else None
    rest = question[start.end() :].strip() if start else ""
    return rest[:1].upper() + rest[1:] if rest else question


def clarify_check(state: AgentState, config: RunnableConfig) -> AgentState:
    """One model call: is the period or measure too vague, with no conventional default?"""
    if state.get("clarifications"):  # already asked once this turn
        return {"clarify": None}
    try:
        out = structured_call(
            "clarify",
            "fast",
            ClarifyOutput,
            clarify_messages(config["configurable"]["role"], state),
        )
    except (LLMBudgetExceededError, LLMConfigError):
        return {"failure": "unavailable"}
    assumptions = list(state.get("assumptions", []))
    if out.default_applied:
        assumptions.append(out.default_applied)
    ask = (
        out.needs_clarification and out.dimension in ("time_window", "measure")
        and out.question and 2 <= len(out.options) <= 4
    )  # fmt: skip
    if ask and out.dimension == "time_window" and not VAGUE_TIME.search(state["message"]):
        # Smoke test: the model asked "which period?" about current-state questions (overdue,
        # stock, status split) and explicit periods ("last 3 quarters"). Only vague wording
        # justifies the question (D-036).
        ask = False
    if ask and out.dimension == "measure" and not VAGUE_MEASURE.search(state["message"]):
        # Smoke run 2: asked "gross, operating or net margin?" when only gross margin exists.
        ask = False
    question = without_echo(out.question or "", state["message"])
    payload = {"kind": "clarify", "dimension": out.dimension, "question": question,
               "options": out.options} if ask else None  # fmt: skip
    return {"clarify": payload, "assumptions": assumptions}


def after_clarify_check(
    state: AgentState,
) -> Literal["ask_clarify", "generate_sql", "respond_failure"]:
    if state.get("failure"):
        return "respond_failure"
    return "ask_clarify" if state.get("clarify") else "generate_sql"


# Free-text answers to "which period?" must look like a period (user-found, 2026-10-03).
PERIOD_WORDS = re.compile(
    r"\d|\b(today|yesterday|week|month|quarter|year|days?|since|till|until|from|to|between|"
    r"jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*|"
    r"aaj|kal|din|hafte|hafta|mahine|mahina|saal)\b|आज|कल|दिन|हफ्त|सप्ताह|महीन|साल",
    re.I,
)


def _usable(dimension: str | None, answer: str, options: list[str]) -> str | None:
    """The answer to use, or None if it can't be understood."""
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return options[int(answer) - 1]
    if any(answer.casefold() == o.casefold() for o in options):
        return answer
    if dimension == "time_window":
        return answer if PERIOD_WORDS.search(answer) else None
    return answer if re.search(r"\w{3,}", answer) else None  # measure: any real words


def ask_clarify(state: AgentState) -> AgentState:
    payload = state["clarify"] or {}
    question = payload.get("question", "")
    if payload.get("unrecognised"):
        question = (
            f"{DIDNT_RECOGNISE[state['lang']].format(answer=payload['unrecognised'])} {question}"
        )
    raw = str(interrupt({k: v for k, v in payload.items() if k != "unrecognised"}
                        | {"question": question})).strip()  # fmt: skip
    options = payload.get("options", [])
    answer = _usable(payload.get("dimension"), raw, options)
    if answer is None:
        if payload.get("unrecognised"):  # second miss: stop, don't guess a period
            return {"clarify": None, "failure": "no_choice"}
        return {"clarify": {**payload, "unrecognised": raw[:60]}}
    clarifications = [*state.get("clarifications", []),
                      {"question": payload.get("question", ""), "answer": answer,
                       "dimension": payload.get("dimension") or ""}]  # fmt: skip
    return {"clarifications": clarifications, "clarify": None}


def after_ask_clarify(
    state: AgentState,
) -> Literal["ask_clarify", "generate_sql", "respond_failure"]:
    if state.get("failure"):
        return "respond_failure"
    return "ask_clarify" if state.get("clarify") else "generate_sql"
