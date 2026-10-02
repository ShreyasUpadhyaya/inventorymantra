"""Run one chat turn through the graph, pause for clarification, and resume it.

LangSmith tags every turn role / lang / route (+ injection_suspected, rbac_denied).
A paused turn is saved under its thread_id; only the user who started it may resume it.
"""

import uuid
from typing import Any, cast

import langsmith
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from pydantic import BaseModel

from app.agent.graph import build_graph
from app.agent.prompts import PROMPT_VERSION
from app.agent.state import AgentState, Turn
from app.agent.tracing import tracing_enabled, user_ref
from app.auth.service import AuthUser
from app.core.config import get_settings


class ThreadAccessError(PermissionError):
    """A user tried to resume a thread they did not start, or one that isn't paused."""


class TurnResult(BaseModel):
    thread_id: str
    route: str  # read | write | refuse_scope | refuse_rbac | help | clarify (paused)
    lang: str
    answer: str
    suggestions: list[str]
    denied_areas: list[str]
    injection_suspected: bool
    entity_mentions: list[dict[str, str]]
    interrupt: dict[str, Any] | None = None  # {kind, dimension, question, options} when paused
    resolved: list[dict[str, str]] = []
    sql: str | None = None
    assumptions: list[str] = []
    columns: list[str] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    failure: str | None = None
    viz: dict[str, Any] | None = None
    answer_checks: dict[str, Any] = {}


def trace_tags(role: str, state: AgentState) -> list[str]:
    tags = [f"role:{role}", f"lang:{state['lang']}", f"route:{state['route']}"]
    if state["guard"].injection_suspected:
        tags.append("injection_suspected")
    if state.get("denied_areas"):
        tags.append("rbac_denied")
    checks = state.get("answer_checks") or {}
    if checks.get("problems"):
        tags.append("answer_rejected")
    if checks.get("fallback") and get_settings().llm_mode != "fake":
        tags.append("fallback_answer")
    return tags


def _config(user: AuthUser, thread_id: str) -> RunnableConfig:
    return {
        "configurable": {"role": user.role, "user_id": str(user.id), "thread_id": thread_id},
        "run_name": "chat_turn",
        "metadata": {"user_ref": user_ref(user.id), "thread_id": thread_id,
                     "prompt_version": PROMPT_VERSION, "llm_mode": get_settings().llm_mode},
    }  # fmt: skip


def fresh_turn(user: AuthUser, message: str, history: list[Turn]) -> AgentState:
    """Reset every per-turn field: a thread's saved state must not leak into a new question."""
    return {
        "owner_ref": user_ref(user.id), "message": message, "history": history,
        "denied_areas": [], "off_topic_part": False, "resolved": [], "entity_issue": None,
        "mention_index": 0, "entity_asks": 0, "clarify": None, "clarifications": [],
        "sql": None, "sql_error": None, "sql_attempts": 0, "assumptions": [], "columns": [],
        "rows": [], "row_count": 0, "failure": None, "answer": "", "suggestions": [],
        "viz": None, "answer_checks": {},
    }  # fmt: skip


def _invoke(user: AuthUser, graph_input: Any, config: RunnableConfig, label: str) -> dict[str, Any]:
    graph = build_graph()
    if not tracing_enabled():
        return cast(dict[str, Any], graph.invoke(graph_input, config=config))
    with langsmith.trace(
        "chat_turn", inputs={"message": label}, tags=[f"role:{user.role}"],
        metadata=config.get("metadata", {}),
    ) as run:  # fmt: skip
        out = cast(dict[str, Any], graph.invoke(graph_input, config=config))
        tags = trace_tags(user.role, cast(AgentState, out))
        if out.get("__interrupt__"):
            tags = [t for t in tags if not t.startswith("route:")] + ["route:clarify"]
        run.add_tags(tags)
        paused = bool(out.get("__interrupt__"))
        run.end(outputs={"answer": out.get("answer"), "paused": paused,
                         "route": "clarify" if paused else out.get("route")})  # fmt: skip
        return out


def _result(thread_id: str, out: dict[str, Any]) -> TurnResult:
    state = cast(AgentState, out)
    guard = state["guard"]
    pause = out.get("__interrupt__")
    payload: dict[str, Any] | None = pause[0].value if pause else None
    return TurnResult(
        thread_id=thread_id,
        route="clarify" if payload else state["route"],
        lang=state["lang"],
        answer=payload["question"] if payload else state.get("answer", ""),
        suggestions=list(payload["options"]) if payload else state.get("suggestions", []),
        denied_areas=state.get("denied_areas", []),
        injection_suspected=guard.injection_suspected,
        entity_mentions=[m.model_dump() for m in guard.entity_mentions],
        interrupt=payload,
        resolved=state.get("resolved", []),
        sql=None if payload else state.get("sql"),
        assumptions=state.get("assumptions", []),
        columns=[] if payload else state.get("columns", []),
        rows=[] if payload else state.get("rows", []),
        row_count=0 if payload else state.get("row_count", 0),
        failure=None if payload else state.get("failure"),
        viz=None if payload else state.get("viz"),
        answer_checks={} if payload else state.get("answer_checks", {}),
    )


def run_turn(
    user: AuthUser, message: str, history: list[Turn] | None = None, thread_id: str | None = None
) -> TurnResult:
    thread_id = thread_id or str(uuid.uuid4())
    config = _config(user, thread_id)
    out = _invoke(user, fresh_turn(user, message, history or []), config, message)
    return _result(thread_id, out)


def pending_question(thread_id: str) -> dict[str, Any] | None:
    snapshot = build_graph().get_state({"configurable": {"thread_id": thread_id}})
    for task in snapshot.tasks:
        for pause in task.interrupts:
            return cast(dict[str, Any], pause.value)
    return None


def resume_turn(user: AuthUser, thread_id: str, answer: str) -> TurnResult:
    """Continue a paused turn with the user's choice (option text, code, number or free text)."""
    snapshot = build_graph().get_state({"configurable": {"thread_id": thread_id}})
    if snapshot.values.get("owner_ref") != user_ref(user.id):
        raise ThreadAccessError("This conversation belongs to someone else.")
    if pending_question(thread_id) is None:
        raise ThreadAccessError("There is no question waiting for an answer in this conversation.")
    config = _config(user, thread_id)
    out = _invoke(user, Command(resume=answer[:200]), config, f"[resume] {answer[:200]}")
    return _result(thread_id, out)
