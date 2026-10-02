"""Agent state and the guard's structured output.

Who the user is (id, role) is NOT in the state: it travels in the graph config
(`configurable`), set by the server from the authenticated session, so nothing produced
from user text or model output can change it.
"""

from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from app.agent.prompts import Lang

Intent = Literal["read", "write", "clarification_reply", "help", "greeting", "out_of_scope"]
DataArea = Literal[
    "stock", "movements", "inbound", "requests", "vendors", "purchase_orders", "prices",
    "payments", "spend", "reorder", "sales", "margins", "users", "approvals", "audit_own",
    "audit_all",
]  # fmt: skip
VizType = Literal["table", "bar", "line", "pie", "kpi", "flow"]
Route = Literal["read", "write", "refuse_scope", "refuse_rbac", "help"]


class EntityMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["item", "vendor", "location", "po"]
    text: str = Field(max_length=120)


class GuardOutput(BaseModel):
    """Exactly the fields in PROMPTS.md §2. All required: OpenAI strict JSON schema mode."""

    model_config = ConfigDict(extra="forbid")

    language: Lang
    intent: Intent
    data_areas: list[DataArea]
    injection_suspected: bool
    explicit_viz: VizType | None
    parts: list[str]
    entity_mentions: list[EntityMention]


class Turn(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class AgentState(TypedDict, total=False):
    message: str  # the user's latest message (untrusted)
    history: list[Turn]  # earlier turns, oldest first (untrusted)
    guard: GuardOutput
    lang: Lang
    route: Route
    denied_areas: list[str]  # mixed requests: areas answered with the fixed denial
    answer: str
    suggestions: list[str]  # clickable follow-ups (help mode)
