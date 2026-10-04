"""Agent state and the guard's structured output.

Who the user is (id, role) is NOT in the state: it travels in the graph config
(`configurable`), set by the server from the authenticated session, so nothing produced
from user text or model output can change it.
"""

from typing import Any, Literal, TypedDict

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
    owner_ref: str  # hashed id of the user who started the thread (resume check)
    message: str  # the user's latest message (untrusted)
    history: list[Turn]  # earlier turns, oldest first (untrusted)
    guard: GuardOutput
    lang: Lang
    route: Route
    denied_areas: list[str]  # mixed requests: areas answered with the fixed denial
    off_topic_part: bool  # the message also asked something out of scope
    # read path (C4.2)
    resolved: list[dict[str, str]]  # matched entities {type, text, code, name}
    entity_issue: dict[str, Any] | None  # clarify / not_found for one mention
    mention_index: int  # next mention to resolve (resumes after a clarification)
    entity_asks: int
    # clarification (C4.3)
    clarify: dict[str, Any] | None  # pending question {kind, dimension, question, options}
    clarifications: list[dict[str, str]]  # answered {question, answer} this turn
    sql: str | None
    sql_error: str | None  # sanitised error fed to the repair attempt
    sql_attempts: int
    assumptions: list[str]
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    failure: str | None  # sql_failed | timeout | unanswerable | unavailable
    # reply (C4.4)
    viz: dict[str, Any] | None  # VizSpec for the UI
    answer_checks: dict[str, Any]  # {fallback, problems} from grounding / prediction
    answer: str
    suggestions: list[str]  # clickable follow-ups (help mode, options)
    # writes (C6.3): {id, status, pending[{action, args}], previews, index, question, asks}
    write: dict[str, Any] | None
    write_events: list[dict[str, Any]]  # from applied writes; notifications in C6.5
