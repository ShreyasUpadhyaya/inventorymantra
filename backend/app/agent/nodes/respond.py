"""Fixed-text replies (no model involved)."""

from langchain_core.runnables import RunnableConfig

from app.agent.prompts import DENIAL, OUT_OF_SCOPE
from app.agent.prompts.help import GREETING, HELP_INTRO, WELCOME, examples
from app.agent.signals import THANKS
from app.agent.state import AgentState


def respond_fixed(state: AgentState, config: RunnableConfig) -> AgentState:
    lang = state["lang"]
    route = state["route"]
    if route == "refuse_scope":
        return {"answer": OUT_OF_SCOPE[lang], "suggestions": []}
    if route == "refuse_rbac":
        return {"answer": DENIAL[lang], "suggestions": []}
    chips = examples(config["configurable"]["role"], lang)
    intro = HELP_INTRO[lang]
    if THANKS.search(state["message"]):
        intro = f"{WELCOME[lang]} {intro}"
    elif state["guard"].intent == "greeting":
        intro = f"{GREETING[lang]} {intro}"
    return {"answer": intro, "suggestions": list(chips)}


def finalize(state: AgentState) -> AgentState:
    """Every reply passes here: add the out-of-scope sentence for an off-topic part."""
    if state.get("off_topic_part") and state["route"] != "refuse_scope":
        extra = OUT_OF_SCOPE[state["lang"]]
        if extra not in state.get("answer", ""):
            return {"answer": f"{state.get('answer', '')} {extra}".strip()}
    return {}
