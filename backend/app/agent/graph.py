"""The LangGraph agent (docs/Architecture.md §2). C4.1: guard + fixed replies.

START -> guard -> refuse_scope | refuse_rbac | help -> respond_fixed -> END
               -> read | write                      -> pending_capability -> END
"""

from functools import lru_cache
from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.nodes.guard import guard_node
from app.agent.nodes.respond import pending_capability, respond_fixed
from app.agent.state import AgentState


def after_guard(state: AgentState) -> Literal["respond_fixed", "pending_capability"]:
    return "pending_capability" if state["route"] in ("read", "write") else "respond_fixed"


@lru_cache
def build_graph() -> CompiledStateGraph:  # type: ignore[type-arg]
    graph = StateGraph(AgentState)
    graph.add_node("guard", guard_node)
    graph.add_node("respond_fixed", respond_fixed)
    graph.add_node("pending_capability", pending_capability)
    graph.add_edge(START, "guard")
    graph.add_conditional_edges("guard", after_guard)
    graph.add_edge("respond_fixed", END)
    graph.add_edge("pending_capability", END)
    return graph.compile()
