"""Run one chat turn through the graph, with LangSmith tags role / lang / route."""

import uuid
from typing import Any, cast

import langsmith
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from app.agent.graph import build_graph
from app.agent.prompts import PROMPT_VERSION
from app.agent.state import AgentState, Turn
from app.agent.tracing import tracing_enabled, user_ref
from app.auth.service import AuthUser
from app.core.config import get_settings


class TurnResult(BaseModel):
    thread_id: str
    route: str
    lang: str
    answer: str
    suggestions: list[str]
    denied_areas: list[str]
    injection_suspected: bool
    entity_mentions: list[dict[str, str]]
    resolved: list[dict[str, str]] = []
    sql: str | None = None
    assumptions: list[str] = []
    columns: list[str] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    failure: str | None = None


def trace_tags(role: str, state: AgentState) -> list[str]:
    tags = [f"role:{role}", f"lang:{state['lang']}", f"route:{state['route']}"]
    if state["guard"].injection_suspected:
        tags.append("injection_suspected")
    if state.get("denied_areas"):
        tags.append("rbac_denied")
    return tags


def run_turn(
    user: AuthUser, message: str, history: list[Turn] | None = None, thread_id: str | None = None
) -> TurnResult:
    thread_id = thread_id or str(uuid.uuid4())
    config: RunnableConfig = {
        "configurable": {"role": user.role, "user_id": str(user.id), "thread_id": thread_id},
        "run_name": "chat_turn",
        "metadata": {"user_ref": user_ref(user.id), "thread_id": thread_id,
                     "prompt_version": PROMPT_VERSION, "llm_mode": get_settings().llm_mode},
    }  # fmt: skip
    initial: AgentState = {"message": message, "history": history or []}
    if tracing_enabled():
        with langsmith.trace(
            "chat_turn", inputs={"message": message}, tags=[f"role:{user.role}"],
            metadata=config["metadata"],
        ) as run:  # fmt: skip
            state = cast(AgentState, build_graph().invoke(initial, config=config))
            run.add_tags(trace_tags(user.role, state))
            run.end(outputs={"answer": state["answer"], "route": state["route"]})
    else:
        state = cast(AgentState, build_graph().invoke(initial, config=config))
    guard = state["guard"]
    return TurnResult(
        thread_id=thread_id,
        route=state["route"],
        lang=state["lang"],
        answer=state["answer"],
        suggestions=state.get("suggestions", []),
        denied_areas=state.get("denied_areas", []),
        injection_suspected=guard.injection_suspected,
        entity_mentions=[m.model_dump() for m in guard.entity_mentions],
        resolved=state.get("resolved", []),
        sql=state.get("sql"),
        assumptions=state.get("assumptions", []),
        columns=state.get("columns", []),
        rows=state.get("rows", []),
        row_count=state.get("row_count", 0),
        failure=state.get("failure"),
    )
