"""Read path (C4.2): resolve entities -> generate SQL -> guard -> execute as the role.

The answer composer, VizSpec and grounding arrive in C4.4 (`present_rows` is the interim
reply) and the clarification interrupt in C4.3 (`respond_entity` asks in plain text now).
"""

import json
import logging
from typing import Any, Literal
from uuid import UUID

import psycopg
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict

from app.agent.entity_match import AREA_FOR_KIND, resolve
from app.agent.fake import OFFLINE_UNSUPPORTED
from app.agent.llm import LLMBudgetExceededError, LLMConfigError, structured_call
from app.agent.nodes.guard import neutralise, system_prompt
from app.agent.prompts import DENIAL, render
from app.agent.prompts.replies import (
    DID_YOU_MEAN,
    FAILURE,
    KIND_WORD,
    NO_CHOICE,
    NOT_FOUND,
    WHICH_ONE,
)
from app.agent.prompts.sql import SQL_TASK
from app.agent.prompts.sql_examples import few_shots
from app.agent.schema_context import allowed_views, schema_block
from app.agent.sql_guard import validate_sql
from app.agent.state import AgentState
from app.core.config import app_today
from app.core.jsonsafe import json_value
from app.db.pool import get_pool
from app.db.readonly import readonly_session

logger = logging.getLogger("inventorymantra.agent")

MAX_SQL_ATTEMPTS = 3  # first try + 2 repairs (Architecture.md §2)
MAX_ROWS = 500


class SqlOutput(BaseModel):
    """PROMPTS.md §4 output. All fields required for OpenAI strict JSON schema."""

    model_config = ConfigDict(extra="forbid")

    sql: str | None
    assumptions: list[str]
    answerable: bool


# ------------------------------------------------------------------ entities
def resolve_entities(state: AgentState, config: RunnableConfig) -> AgentState:
    """Resolve mentions in order, resuming from `mention_index` after a clarification."""
    role = config["configurable"]["role"]
    mentions = state["guard"].entity_mentions
    index = state.get("mention_index", 0)
    resolved = list(state.get("resolved", []))
    denied = list(state.get("denied_areas", []))
    with get_pool().connection() as conn:
        while index < len(mentions):
            mention = mentions[index]
            result = resolve(conn, role, mention.type, mention.text)
            if result.outcome == "match":
                top = result.candidates[0]
                resolved.append({"type": mention.type, "text": mention.text,
                                 "code": top.code, "name": top.name})  # fmt: skip
            elif result.outcome == "denied":
                denied.append(AREA_FOR_KIND[mention.type])
            else:  # clarify -> ask (interrupt); not_found -> reply with suggestions
                return {
                    "entity_issue": {
                        "outcome": result.outcome, "type": mention.type, "text": mention.text,
                        "options": [c.name for c in result.candidates],
                        "codes": [c.code for c in result.candidates],
                    },
                    "mention_index": index,
                    "resolved": resolved,
                    "denied_areas": sorted(set(denied)),
                }  # fmt: skip
            index += 1
    allowed_left = set(state["guard"].data_areas) - set(denied)
    if denied and not allowed_left and not resolved:
        return {"route": "refuse_rbac", "denied_areas": sorted(set(denied)), "resolved": []}
    return {"resolved": resolved, "denied_areas": sorted(set(denied)), "entity_issue": None,
            "mention_index": index}  # fmt: skip


def after_entities(
    state: AgentState,
) -> Literal["respond_fixed", "respond_entity", "ask_entity", "clarify_check"]:
    if state.get("route") == "refuse_rbac":
        return "respond_fixed"
    issue = state.get("entity_issue")
    if issue:
        return "ask_entity" if issue["outcome"] == "clarify" else "respond_entity"
    return "clarify_check"


def respond_entity(state: AgentState) -> AgentState:
    issue = state["entity_issue"] or {}
    lang = state["lang"]
    if issue["outcome"] == "clarify":
        answer = WHICH_ONE[lang].format(text=issue["text"])
    elif issue["outcome"] == "no_choice":
        answer = NO_CHOICE[lang].format(text=issue["text"])
    else:
        answer = NOT_FOUND[lang].format(kind=KIND_WORD[lang][issue["type"]], text=issue["text"])
        if issue["options"]:
            answer = f"{answer} {DID_YOU_MEAN[lang]}"
    return {"answer": answer, "suggestions": list(issue["options"])}


# ------------------------------------------------------------------ SQL
def effective_message(state: AgentState) -> str:
    """The user's question plus their clarification answers, all as user text."""
    answers = state.get("clarifications", [])
    if not answers:
        return state["message"]
    return f"{state['message']} [{'; '.join(a['answer'] for a in answers)}]"


def sql_messages(role: str, state: AgentState) -> list[Any]:
    task = render(
        SQL_TASK,
        schema=schema_block(role),
        today=app_today().strftime("%A %d %b %Y"),
        few_shots=few_shots(role),  # type: ignore[arg-type]
    )
    data = json.dumps(state.get("resolved", []), ensure_ascii=False)
    message = effective_message(state)
    user = f"<user_message>{neutralise(message)}</user_message><data>{data}</data>"
    if state.get("sql_error"):
        user += (
            f"<previous_sql>{neutralise(state.get('sql') or '')}</previous_sql>"
            f"<error>{state['sql_error']}</error>"
        )
    return [SystemMessage(system_prompt(role, task)), HumanMessage(user)]


def generate_sql(state: AgentState, config: RunnableConfig) -> AgentState:
    role = config["configurable"]["role"]
    attempts = state.get("sql_attempts", 0) + 1
    try:
        out = structured_call("sql", "reasoning", SqlOutput, sql_messages(role, state))
    except (LLMBudgetExceededError, LLMConfigError) as exc:
        logger.warning("model unavailable: %s", exc.__class__.__name__)
        return {"failure": "unavailable", "sql_attempts": attempts}
    if not out.answerable or not out.sql:
        offline = OFFLINE_UNSUPPORTED in out.assumptions  # honest: a demo limit, not missing data
        kind = "offline" if offline else "unanswerable"
        return {"failure": kind, "assumptions": out.assumptions, "sql_attempts": attempts}
    return {"sql": out.sql, "assumptions": out.assumptions, "sql_attempts": attempts,
            "failure": None}  # fmt: skip


def execute_sql(state: AgentState, config: RunnableConfig) -> AgentState:
    role = config["configurable"]["role"]
    checked = validate_sql(state.get("sql") or "", allowed_views(role))
    if not checked.ok:
        # Fixed reason code only: never echo relation names (CLAUDE.md §5).
        return {"sql_error": f"rejected by the SQL guard: {checked.reason}. Use only the listed "
                             "relations, one SELECT, no comments."}  # fmt: skip
    user_id = UUID(config["configurable"]["user_id"])
    try:
        with readonly_session(role, user_id, app_today()) as conn:
            cursor = conn.execute(checked.sql or "")
            columns = [d.name for d in cursor.description or []]
            rows = [[json_value(v) for v in row] for row in cursor.fetchmany(MAX_ROWS)]
    except psycopg.errors.QueryCanceled:
        return {"failure": "timeout", "sql": checked.sql}
    except psycopg.Error as exc:
        detail = (str(exc).splitlines() or [""])[0][:200]  # role-scoped names only (guarded)
        return {"sql_error": f"database error {exc.sqlstate}: {detail}", "sql": checked.sql}
    return {"sql": checked.sql, "sql_error": None, "columns": columns, "rows": rows,
            "row_count": len(rows)}  # fmt: skip


def after_sql(state: AgentState) -> Literal["execute_sql", "respond_failure"]:
    return "respond_failure" if state.get("failure") else "execute_sql"


def after_execute(state: AgentState) -> Literal["compose", "generate_sql", "respond_failure"]:
    if state.get("failure"):
        return "respond_failure"
    if state.get("sql_error"):
        return (
            "generate_sql" if state.get("sql_attempts", 0) < MAX_SQL_ATTEMPTS else "respond_failure"
        )
    return "compose"


def respond_failure(state: AgentState) -> AgentState:
    kind = state.get("failure") or "sql_failed"
    answer = FAILURE[kind][state["lang"]]
    if state.get("denied_areas"):
        answer = f"{answer} {DENIAL[state['lang']]}"
    return {"answer": answer, "suggestions": [], "failure": kind}
