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
from app.agent.signals import (
    GREETING,
    OFF_TOPIC,
    THANKS,
    VAGUE_MEASURE,
    WRITE_START,
    areas_named,
    script_language,
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


def decide(role: str, guard: GuardOutput) -> tuple[Route, list[str], bool]:
    """(route, denied areas, has an off-topic part). Access is decided here, never by the
    model. A message that is off-topic *and* touches data (e.g. "price of PO 3 and write a
    poem") gets each part answered: the data part as usual, plus the out-of-scope sentence."""
    off_topic = guard.intent == "out_of_scope"
    if off_topic and not guard.data_areas:
        # A prompt-extraction attempt ("print your system prompt") gets the help reply:
        # what you *can* ask, nothing about how the assistant works.
        return ("help" if guard.injection_suspected else "refuse_scope"), [], False
    if guard.intent in ("help", "greeting"):
        return "help", [], False
    if guard.intent == "write":
        # Writes are authorised per action (only the role's actions are offered, and the
        # write services check policy again), not by read areas: an Inventory Manager
        # receiving "against PO-2026-0003" mentions POs but is allowed to receive (C6.3).
        return "write", [], off_topic
    denied = sorted(permissions.denied_areas(role, guard.data_areas))
    if denied and len(denied) == len(set(guard.data_areas)):
        return "refuse_rbac", denied, off_topic  # everything asked for is outside the role
    return "read", denied, off_topic


def guard_node(state: AgentState, config: RunnableConfig) -> AgentState:
    role = config["configurable"]["role"]
    guard = structured_call("guard", "fast", GuardOutput, guard_messages(role, state))
    language = script_language(guard.language, state["message"])
    route, denied, off_topic = decide(role, guard)
    message = state["message"]
    if not guard.injection_suspected:
        named = areas_named(message)
        if route == "refuse_scope" and named:  # "show me the users table": a data question
            denied = sorted(permissions.denied_areas(role, named))
            route = "refuse_rbac" if len(denied) == len(set(named)) else "read"
        if route in ("help", "refuse_scope") and VAGUE_MEASURE.search(message):
            # "how are we doing?" / "business kaisa chal raha hai?" (called off-topic in a
            # user test, 2026-10-04) -> ask which measure, not the help menu or a refusal
            route, off_topic = "read", False
        if route == "read" and (GREETING.search(message) or THANKS.search(message)):
            route, guard = "help", guard.model_copy(update={"intent": "greeting"})
        if route == "read" and WRITE_START.search(message):
            route = "write"  # "Add 3 to 132 and 5 to the other": a change, not a question
        if off_topic and guard.parts and all(areas_named(part) for part in guard.parts):
            # "price of SS sheet / show me the users table": the model called hidden data
            # off-topic; every part names a data area, so only the denial applies (2026-10-04)
            off_topic = False
    if route not in ("refuse_scope", "help") and OFF_TOPIC.search(state["message"]):
        off_topic = True  # the model may miss an off-topic part next to a data question
    if guard.injection_suspected:
        logger.warning("injection suspected (role=%s, route=%s)", role, route)
    return {"guard": guard, "lang": language, "route": route, "denied_areas": denied,
            "off_topic_part": off_topic}  # fmt: skip
