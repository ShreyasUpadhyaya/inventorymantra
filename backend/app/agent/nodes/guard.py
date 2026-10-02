"""Guard/router node (PROMPTS.md §2): classify the message, then decide access IN CODE.

Message assembly follows PROMPTS.md §0: the system message is built only from trusted
server text; the user's words (and earlier turns) go in the user message inside tags.
"""

import logging
import re

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from app.agent.llm import structured_call
from app.agent.prompts import (
    CORE_SYSTEM_PROMPT,
    GUARD_TASK,
    ROLE_CAPABILITIES,
    ROLE_LABELS,
    render,
)
from app.agent.state import AgentState, GuardOutput, Route
from app.core.config import TIMEZONE, app_today, get_settings
from app.policy import permissions

logger = logging.getLogger("inventorymantra.agent")

HISTORY_LIMIT = 6  # most recent messages given to the guard (PROMPTS.md §2)
# Our own tag names; user text must not be able to open or close them.
_TAGS = re.compile(
    r"</?\s*(user_message|data|answer|assumptions|denied_parts|previous_sql|error|"
    r"result_shape|explicit_viz)\b[^>]*>",
    re.I,
)


def neutralise(text: str) -> str:
    return _TAGS.sub("", text)


def system_prompt(role: str, task: str) -> str:
    """CORE + task block, filled with trusted values only."""
    capabilities = render(
        ROLE_CAPABILITIES[role],
        approval_threshold=f"{get_settings().po_approval_threshold_inr:,}",
    )
    core = render(
        CORE_SYSTEM_PROMPT,
        role_label=ROLE_LABELS[role],
        role_capabilities=capabilities,
        today=app_today().strftime("%d %b %Y"),
        timezone=TIMEZONE.key,
    )
    return f"{core}\n\n{task}"


def guard_messages(role: str, state: AgentState) -> list[BaseMessage]:
    messages: list[BaseMessage] = [SystemMessage(system_prompt(role, GUARD_TASK))]
    for turn in state.get("history", [])[-HISTORY_LIMIT:]:
        if turn["role"] == "user":
            messages.append(
                HumanMessage(f"<user_message>{neutralise(turn['content'])}</user_message>")
            )
        else:
            messages.append(AIMessage(turn["content"]))
    messages.append(HumanMessage(f"<user_message>{neutralise(state['message'])}</user_message>"))
    return messages


def decide(role: str, guard: GuardOutput) -> tuple[Route, list[str]]:
    """Route and denied areas. Access is decided here, never by the model."""
    if guard.intent == "out_of_scope":
        return "refuse_scope", []
    if guard.intent in ("help", "greeting"):
        return "help", []
    denied = sorted(permissions.denied_areas(role, guard.data_areas))
    if denied and len(denied) == len(set(guard.data_areas)):
        return "refuse_rbac", denied  # everything asked for is outside the role
    return ("write" if guard.intent == "write" else "read"), denied


def guard_node(state: AgentState, config: RunnableConfig) -> AgentState:
    role = config["configurable"]["role"]
    guard = structured_call("guard", "fast", GuardOutput, guard_messages(role, state))
    route, denied = decide(role, guard)
    if guard.injection_suspected:
        logger.warning("injection suspected (role=%s, route=%s)", role, route)
    return {"guard": guard, "lang": guard.language, "route": route, "denied_areas": denied}
