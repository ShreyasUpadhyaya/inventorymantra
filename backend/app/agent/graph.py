"""The LangGraph agent (docs/Architecture.md §2).

START -> guard -> refuse_scope | refuse_rbac | help --------------------> respond_fixed
               -> write ----------------------------------------------> pending_capability
               -> read -> resolve_entities -> clarify/not_found -----> respond_entity
                                           -> denied (nothing left) --> respond_fixed
                                           -> generate_sql -> execute_sql -> compose
                                                   ^              | error, < 3 attempts
                                                   +--------------+
                                              failure / attempts used -> respond_failure
    every reply -> finalize (adds the out-of-scope sentence for an off-topic part) -> END
"""

from functools import lru_cache
from typing import Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.nodes.clarify import (
    after_ask_clarify,
    after_ask_entity,
    after_clarify_check,
    ask_clarify,
    ask_entity,
    clarify_check,
)
from app.agent.nodes.compose import compose
from app.agent.nodes.guard import guard_node
from app.agent.nodes.read import (
    after_entities,
    after_execute,
    after_sql,
    execute_sql,
    generate_sql,
    resolve_entities,
    respond_entity,
    respond_failure,
)
from app.agent.nodes.respond import finalize, pending_capability, respond_fixed
from app.agent.state import AgentState


def after_guard(
    state: AgentState,
) -> Literal["respond_fixed", "pending_capability", "resolve_entities"]:
    if state["route"] == "read":
        return "resolve_entities"
    return "pending_capability" if state["route"] == "write" else "respond_fixed"


# Paused (interrupted) turns are saved per thread. In-process for now; C4.5 moves this to
# Postgres so a pause survives restarts and works across API workers.
# Only our own state models may be rebuilt from a saved checkpoint (no arbitrary types).
CHECKPOINT_TYPES = [("app.agent.state", "GuardOutput"), ("app.agent.state", "EntityMention")]
_serde = JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)
_checkpointer: BaseCheckpointSaver = InMemorySaver(serde=_serde)  # type: ignore[type-arg]


@lru_cache
def build_graph() -> CompiledStateGraph:  # type: ignore[type-arg]
    graph = StateGraph(AgentState)
    for name, node in [
        ("guard", guard_node),
        ("respond_fixed", respond_fixed),
        ("pending_capability", pending_capability),
        ("resolve_entities", resolve_entities),
        ("respond_entity", respond_entity),
        ("ask_entity", ask_entity),
        ("clarify_check", clarify_check),
        ("ask_clarify", ask_clarify),
        ("generate_sql", generate_sql),
        ("execute_sql", execute_sql),
        ("compose", compose),
        ("respond_failure", respond_failure),
        ("finalize", finalize),
    ]:
        graph.add_node(name, node)  # type: ignore[call-overload]
    graph.add_edge(START, "guard")
    graph.add_conditional_edges("guard", after_guard)
    graph.add_conditional_edges("resolve_entities", after_entities)
    graph.add_conditional_edges("ask_entity", after_ask_entity)
    graph.add_conditional_edges("clarify_check", after_clarify_check)
    graph.add_conditional_edges("ask_clarify", after_ask_clarify)
    graph.add_conditional_edges("generate_sql", after_sql)
    graph.add_conditional_edges("execute_sql", after_execute)
    for terminal in ("respond_fixed", "pending_capability", "respond_entity", "compose",
                     "respond_failure"):  # fmt: skip
        graph.add_edge(terminal, "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=_checkpointer)
