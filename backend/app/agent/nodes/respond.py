"""Fixed-text replies (no model involved) and the placeholder for unbuilt routes."""

from langchain_core.runnables import RunnableConfig

from app.agent.prompts import DENIAL, OUT_OF_SCOPE
from app.agent.prompts.help import EXAMPLES, GREETING, HELP_INTRO
from app.agent.state import AgentState

PENDING = {
    "en": "I understood your question, but answering it is still being built in this version.",
    "hi": "मैंने आपका सवाल समझ लिया है, लेकिन इसका उत्तर देने की सुविधा अभी बन रही है।",
    "hinglish": (
        "Maine aapka sawaal samajh liya hai, lekin iska jawab dene ki suvidha abhi ban rahi hai."
    ),
}


def respond_fixed(state: AgentState, config: RunnableConfig) -> AgentState:
    lang = state["lang"]
    route = state["route"]
    if route == "refuse_scope":
        return {"answer": OUT_OF_SCOPE[lang], "suggestions": []}
    if route == "refuse_rbac":
        return {"answer": DENIAL[lang], "suggestions": []}
    examples = EXAMPLES[config["configurable"]["role"]]
    intro = HELP_INTRO[lang]
    if state["guard"].intent == "greeting":
        intro = f"{GREETING[lang]} {intro}"
    return {"answer": intro, "suggestions": list(examples)}


def finalize(state: AgentState) -> AgentState:
    """Every reply passes here: add the out-of-scope sentence for an off-topic part."""
    if state.get("off_topic_part") and state["route"] != "refuse_scope":
        extra = OUT_OF_SCOPE[state["lang"]]
        if extra not in state.get("answer", ""):
            return {"answer": f"{state.get('answer', '')} {extra}".strip()}
    return {}


def pending_capability(state: AgentState) -> AgentState:
    """Read/write/clarify nodes arrive in C4.2-C4.4 and C6; until then say so honestly."""
    answer = PENDING[state["lang"]]
    if state.get("denied_areas"):
        answer = f"{answer} {DENIAL[state['lang']]}"
    return {"answer": answer, "suggestions": []}
