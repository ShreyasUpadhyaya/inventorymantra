"""Chat writes (C6.3; docs/WRITE_PATH.md §2, PROMPTS.md §7):

  plan_write (model: which action, which fields)
    -> check_write (server: ask for what's missing, match names, preview each action)
         -> ask_write    (pause: ONE question; the answer fills one field) -> check_write
         -> confirm_write (pause: one card with every preview)  -> apply_write
    -> finalize

The model only picks the action and copies the user's words. Missing details are asked one
at a time, names are matched by the server, and nothing is written until the user confirms
the card; then all actions are saved in one transaction (all-or-nothing). As in the
clarify nodes, each node that pauses does nothing else, because LangGraph re-runs a paused
node from its first line on resume.
"""

import re
import uuid
from datetime import date, timedelta
from typing import Any, Literal
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict

from app.agent.llm import LLMBudgetExceededError, LLMConfigError, structured_call
from app.agent.nodes.guard import neutralise, system_prompt
from app.agent.prompts import DENIAL, Lang
from app.agent.prompts.write import ACTION_LINES, WRITE_TASK
from app.agent.state import AgentState
from app.agent.write_text import (
    CANCELLED,
    CHOICES,
    CONFIRM_OPTIONS,
    CONFIRM_QUESTION,
    CONFIRM_WORDS,
    DIDNT_CATCH,
    FIELD_QUESTIONS,
    GAVE_UP,
    GENERIC_QUESTION,
    NOT_A_CHANGE,
    OFFLINE_WRITE,
    field_name,
)
from app.auth.service import AuthUser, load_user
from app.core.config import app_today, get_settings
from app.db.pool import get_pool
from app.policy import permissions
from app.services.writes import Preview, WriteError, apply_batch, cancel, preview
from app.services.writes.framework import Ambiguous, NotFound

ActionName = Literal[
    "record_stock_movement", "transfer_stock", "receive_against_po",
    "record_unexpected_receipt", "raise_stock_request", "create_purchase_order",
    "update_po_expected_date", "cancel_po", "clear_payment_tranche", "set_reorder_policy",
    "create_vendor", "decide_approval", "ask_about_approval", "reply_to_approval",
    "create_user", "deactivate_user",
]  # fmt: skip
MAX_ASKS = 2  # an unusable answer is asked once more, then the change is dropped
HISTORY_TURNS = 6


# ------------------------------------------------------------------ model output (§7)
class LineOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: str | None
    quantity: float | None
    unit_price: float | None
    tax_percentage: float | None


class WriteCallOut(BaseModel):
    """One action. Every field is required-but-nullable (OpenAI strict JSON schema): the
    model leaves a field empty rather than guessing, and the server asks for it."""

    model_config = ConfigDict(extra="forbid")

    action: ActionName
    kind: Literal["inbound", "outbound", "issue"] | None
    item: str | None
    quantity: float | None
    location: str | None
    issued_to: str | None
    from_location: str | None
    to_location: str | None
    po: str | None
    vendor: str | None
    vendor_name: str | None
    lines: list[LineOut] | None
    expected_delivery_date: str | None
    delivery_location: str | None
    needed_by: str | None
    reason: str | None
    stage: str | None
    min_level: float | None
    reorder_qty: float | None
    lead_time_days: int | None
    safety_stock: float | None
    preferred_vendor: str | None
    name: str | None
    city: str | None
    payment_terms: str | None
    decision: Literal["approve", "reject"] | None
    message: str | None
    full_name: str | None
    email: str | None
    role: Literal["inventory_manager", "procurement_manager", "owner"] | None
    user: str | None
    note: str | None


class WritePlanOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[WriteCallOut]
    none_applicable: bool


# Details that must be known before a preview, in the order they are asked.
REQUIRED: dict[str, list[str]] = {
    "record_stock_movement": ["kind", "item", "quantity", "location"],
    "transfer_stock": ["item", "quantity", "from_location", "to_location"],
    "receive_against_po": ["po", "quantity", "location"],
    "record_unexpected_receipt": ["item", "quantity", "location"],
    "raise_stock_request": ["item", "quantity"],
    "create_purchase_order": ["vendor", "lines"],
    "update_po_expected_date": ["po", "expected_delivery_date"],
    "cancel_po": ["po"],
    "clear_payment_tranche": ["po"],
    "set_reorder_policy": ["item", "min_level", "reorder_qty", "lead_time_days"],
    "create_vendor": ["name"],
    "decide_approval": ["po", "decision"],
    "ask_about_approval": ["po", "message"],
    "reply_to_approval": ["po", "message"],
    "create_user": ["full_name", "email", "role"],
    "deactivate_user": ["user"],
}
LINE_REQUIRED = ["item", "quantity", "unit_price"]
NUMBER_FIELDS = {"quantity", "unit_price", "tax_percentage", "min_level", "reorder_qty",
                 "lead_time_days", "safety_stock"}  # fmt: skip
DATE_FIELDS = {"expected_delivery_date", "needed_by"}
LOCATION_FIELDS = {"location", "from_location", "to_location", "delivery_location"}


# ------------------------------------------------------------------ helpers
def _user(config: RunnableConfig, lang: Lang) -> AuthUser:
    """The signed-in user, read again from the database: a deactivated or unknown account
    gets the fixed denial, never a write."""
    with get_pool().connection() as conn:
        user = load_user(conn, UUID(config["configurable"]["user_id"]))
    if user is None or not user.is_active:
        raise WriteError(DENIAL[lang])
    return user


def _get(args: dict[str, Any], path: str) -> Any:
    node: Any = args
    for part in path.split("."):
        if isinstance(node, list):
            node = node[int(part)] if int(part) < len(node) else None
        elif isinstance(node, dict):
            node = node.get(part)
        if node is None:
            return None
    return node


def _set(args: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node: Any = args
    for i, part in enumerate(parts[:-1]):
        nxt: Any = [] if parts[i + 1].isdigit() else {}
        if isinstance(node, list):
            while len(node) <= int(part):
                node.append({})
            node = node[int(part)]
        else:
            node = node.setdefault(part, nxt)
    if isinstance(node, list):
        while len(node) <= int(parts[-1]):
            node.append({})
        node[int(parts[-1])] = value
    else:
        node[parts[-1]] = value


def missing_field(action: str, args: dict[str, Any]) -> str | None:
    """The next detail to ask for, or None when the action can be previewed."""
    for field in REQUIRED[action]:
        if field == "lines":
            lines = args.get("lines") or [{}]
            for i, line in enumerate(lines):
                for sub in LINE_REQUIRED:
                    if line.get(sub) in (None, ""):
                        return f"lines.{i}.{sub}"
        elif args.get(field) in (None, ""):
            return field
    if action == "record_stock_movement" and args.get("kind") == "issue" \
            and not args.get("issued_to"):  # fmt: skip
        return "issued_to"
    return None


def _args(call: WriteCallOut) -> dict[str, Any]:
    raw = call.model_dump(exclude={"action"})
    args = {k: v for k, v in raw.items() if v not in (None, "", []) and k != "lines"}
    if call.lines:
        args["lines"] = [{k: v for k, v in ln.items() if v is not None} for ln in raw["lines"]]
    return args


def _options(field: str, lang: Lang) -> tuple[list[str], list[str]]:
    base = field.rsplit(".", 1)[-1]
    if base in CHOICES:
        pairs = CHOICES[base][lang]
        return [label for label, _ in pairs], [value for _, value in pairs]
    if base in LOCATION_FIELDS or base == "issued_to":
        table = "inv_locations" if base != "issued_to" else "inv_issued_to_targets"
        with get_pool().connection() as conn:
            rows = conn.execute(
                f"SELECT name FROM {table} WHERE is_active ORDER BY name"  # noqa: S608 (fixed table)
            ).fetchall()
        names = [r[0] for r in rows]
        return names, names
    return [], []


def _question(field: str, args: dict[str, Any], lang: Lang) -> str:
    base = field.rsplit(".", 1)[-1]
    item = _get(args, field.rsplit(".", 1)[0] + ".item") if "." in field else args.get("item")
    template = FIELD_QUESTIONS.get(base)
    if template is None:
        return GENERIC_QUESTION[lang].format(field=field_name(field, lang))
    of_item = (f" of {item}" if lang == "en" else f" ({item})") if item else ""
    of_item_hi = item or {"en": "the item", "hi": "इस आइटम", "hinglish": "is item"}[lang]
    if lang == "hi" and item:
        of_item_hi = f"{item} की"
    return template[lang].format(of_item=of_item, of_item_hi=of_item_hi)


NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
MONTH_NAMES = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
MONTHS = {m: i for i, m in enumerate(MONTH_NAMES, 1)}


def parse_number(text: str) -> float | None:
    found = NUMBER.search(text.replace("₹", ""))
    return float(found.group(0).replace(",", "")) if found else None


def parse_date(text: str, today: date) -> date | None:
    """YYYY-MM-DD, DD/MM/YYYY, "13 Oct [2026]", "Oct 13", today/tomorrow (aaj/kal)."""
    t = text.strip().lower()
    if t in ("today", "aaj", "आज"):
        return today
    if t in ("tomorrow", "kal", "कल"):
        return today + timedelta(days=1)
    try:
        if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", t):
            return date(int(m[3]), int(m[2]), int(m[1]))
        m = re.fullmatch(r"(\d{1,2})\s*([a-z]{3})[a-z]*\.?(?:\s+(\d{4}))?", t) or None
        if m and m[2] in MONTHS:
            return _with_year(int(m[1]), MONTHS[m[2]], m[3], today)
        m = re.fullmatch(r"([a-z]{3})[a-z]*\.?\s*(\d{1,2})(?:,?\s+(\d{4}))?", t) or None
        if m and m[1] in MONTHS:
            return _with_year(int(m[2]), MONTHS[m[1]], m[3], today)
    except ValueError:
        return None
    return None


def _with_year(day: int, month: int, year: str | None, today: date) -> date:
    if year:
        return date(int(year), month, day)
    guess = date(today.year, month, day)
    return guess if guess >= today else date(today.year + 1, month, day)  # dates ahead


def _w(state: AgentState) -> dict[str, Any]:
    return dict(state.get("write") or {})


def _offline() -> bool:
    return get_settings().llm_mode == "fake"


# ------------------------------------------------------------------ nodes
def write_messages(role: str, state: AgentState) -> list[Any]:
    allowed = [line for name, line in ACTION_LINES.items() if permissions.can(role, name)]
    system = f"{system_prompt(role, WRITE_TASK)}\n<actions>\n" + "\n".join(allowed) + "\n</actions>"
    turns = state.get("history", [])[-HISTORY_TURNS:]
    history = "".join(f"<{t['role']}>{neutralise(t['content'])}</{t['role']}>" for t in turns)
    user = (
        f"<history>{history}</history><user_message>{neutralise(state['message'])}</user_message>"
    )
    return [SystemMessage(system), HumanMessage(user)]


def plan_write(state: AgentState, config: RunnableConfig) -> AgentState:
    role, lang = config["configurable"]["role"], state["lang"]
    try:
        out = structured_call("write", "reasoning", WritePlanOut, write_messages(role, state))
    except (LLMBudgetExceededError, LLMConfigError):
        return {"failure": "unavailable", "write": {"status": "error"},
                "answer": NOT_A_CHANGE[lang]}  # fmt: skip
    allowed = [c for c in out.actions if permissions.can(role, c.action)]
    if not allowed:
        denied = permissions.denied_areas(role, state["guard"].data_areas)
        if out.actions or denied:  # it asks for a change this role can't make
            return {"route": "refuse_rbac", "write": {"status": "error"},
                    "answer": DENIAL[lang], "suggestions": []}  # fmt: skip
        answer = OFFLINE_WRITE[lang] if _offline() else NOT_A_CHANGE[lang]
        return {"write": {"status": "error"}, "answer": answer, "suggestions": []}
    pending = [{"action": c.action, "args": _args(c)} for c in allowed]
    return {"write": {"id": str(uuid.uuid4()), "status": "checking", "pending": pending,
                      "previews": [], "index": 0, "question": None, "asks": 0}}  # fmt: skip


def check_write(state: AgentState, config: RunnableConfig) -> AgentState:
    """Ask for the next missing detail, or preview the next action; repeat until a question
    is needed or every action has a preview."""
    write = _w(state)
    lang = state["lang"]
    pending = [dict(p) for p in write["pending"]]
    previews = list(write["previews"])
    index = write["index"]
    try:
        user = _user(config, lang)
    except WriteError as exc:  # was uncaught: the turn crashed instead of answering
        return _stop(write, exc.message)
    while index < len(pending):
        action, args = pending[index]["action"], pending[index]["args"]
        field = missing_field(action, args)
        if field == "item" and index and pending[index - 1]["action"] == action:
            # "add 3 AC contactor to 132-2 and 5 to 132-1": the second part means the same item
            earlier = pending[index - 1]["args"].get("item")
            if earlier:
                args["item"] = earlier
                field = missing_field(action, args)
        if field:
            options, values = _options(field, lang)
            question = {"field": field, "text": _question(field, args, lang), "options": options,
                        "values": values, "prefix": None}  # fmt: skip
            return {"write": {**write, "pending": pending, "previews": previews, "index": index,
                              "status": "question", "question": question}}  # fmt: skip
        try:
            previews.append(preview(user, action, args).model_dump(mode="json"))
        except (Ambiguous, NotFound) as exc:
            if exc.field is None:
                return _stop(write, exc.message)
            misses = dict(write.get("misses") or {})
            misses[exc.field] = misses.get(exc.field, 0) + 1
            write = {**write, "misses": misses}
            if isinstance(exc, NotFound) and misses[exc.field] > MAX_ASKS:
                # probe 2026-10-04: a vendor answer of "1" matched nothing, was asked again
                # and accepted again, forever; now the change is dropped after two retries
                return _stop(write, GAVE_UP[lang].format(field=field_name(exc.field, lang)))
            if isinstance(exc, NotFound):
                _set(args, exc.field, None)  # the name matched nothing: ask for it again
            question = {"field": exc.field, "text": exc.message if exc.options else
                        f"{exc.message} {_question(exc.field, args, lang)}",
                        "options": exc.options, "values": exc.values, "prefix": None}  # fmt: skip
            return {"write": {**write, "pending": pending, "previews": previews, "index": index,
                              "status": "question", "question": question}}  # fmt: skip
        except WriteError as exc:
            if exc.field and exc.code == "invalid" and _get(args, exc.field) in (None, ""):
                question = {"field": exc.field, "text": _question(exc.field, args, lang),
                            "options": [], "values": [], "prefix": None}  # fmt: skip
                return {"write": {**write, "pending": pending, "index": index,
                                  "status": "question", "question": question}}  # fmt: skip
            return _stop(write, exc.message)
        index += 1
    return {"write": {**write, "pending": pending, "previews": previews, "index": index,
                      "status": "ready", "question": None}}  # fmt: skip


def _stop(write: dict[str, Any], message: str) -> AgentState:
    return {"write": {**write, "status": "error"}, "answer": message, "suggestions": []}


def ask_write(state: AgentState) -> AgentState:
    write = _w(state)
    q = write["question"]
    text = f"{q['prefix']} {q['text']}" if q.get("prefix") else q["text"]
    answer = str(interrupt({"kind": "clarify", "dimension": "write", "question": text,
                            "options": q["options"]})).strip()  # fmt: skip
    lang = state["lang"]
    field, base = q["field"], q["field"].rsplit(".", 1)[-1]
    value: Any = None
    problem = "choice"
    picked = _pick(q, answer)
    if picked is not None:
        value = picked
    elif base in ("kind", "stage", "decision", "role"):
        value = None  # these must be one of the offered options
    elif base in NUMBER_FIELDS:
        number = parse_number(answer)
        value = int(number) if number is not None and base == "lead_time_days" else number
        problem = "number"
    elif base in DATE_FIELDS:
        parsed = parse_date(answer, app_today())
        value = parsed.isoformat() if parsed else None
        problem = "date"
    else:
        value = answer[:120] or None
    asks = write.get("asks", 0) + 1
    if value is None:
        if asks >= MAX_ASKS:
            return {"write": {**write, "status": "error", "asks": asks},
                    "answer": GAVE_UP[lang].format(field=field_name(field, lang)),
                    "suggestions": []}  # fmt: skip
        again = {**q, "prefix": DIDNT_CATCH[problem][lang]}
        return {"write": {**write, "question": again, "asks": asks}}
    pending = [dict(p) for p in write["pending"]]
    args = dict(pending[write["index"]]["args"])
    if args.get("lines") is not None:
        args["lines"] = [dict(line) for line in args["lines"]]
    _set(args, field, value)
    pending[write["index"]] = {**pending[write["index"]], "args": args}
    return {"write": {**write, "pending": pending, "status": "checking", "question": None,
                      "asks": 0}}  # fmt: skip


def _pick(q: dict[str, Any], answer: str) -> str | None:
    """An offered option, by its text or 1-based number."""
    options, values = q.get("options") or [], q.get("values") or []
    if not options:
        return None
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return str(values[int(answer) - 1])
    for option, value in zip(options, values, strict=True):
        if answer.casefold() in (option.casefold(), str(value).casefold()):
            return str(value)
    return None


def confirm_write(state: AgentState) -> AgentState:
    write = _w(state)
    lang = state["lang"]
    yes, no = CONFIRM_OPTIONS[lang]
    cards = [{"action": p["action"], "summary": p["summary"], "fields": p["fields"],
              "effects": p["effects"]} for p in write["previews"]]  # fmt: skip
    answer = str(interrupt({"kind": "confirm", "question": CONFIRM_QUESTION[lang],
                            "options": [yes, no], "previews": cards})).strip()  # fmt: skip
    confirmed = answer.casefold() in {w.casefold() for w in CONFIRM_WORDS} | {yes.casefold()}
    return {"write": {**write, "status": "confirmed" if confirmed else "cancelled"}}


def apply_write(state: AgentState, config: RunnableConfig) -> AgentState:
    write = _w(state)
    lang = state["lang"]
    previews = [Preview.model_validate(p) for p in write["previews"]]
    operation_id = f"chat:{config['configurable']['thread_id']}:{write['id']}"
    thread_id = config["configurable"]["thread_id"]
    try:
        user = _user(config, lang)
        if write["status"] != "confirmed":
            cancel(user, previews, operation_id, thread_id)
            return {"write": {**write, "status": "done"}, "answer": CANCELLED[lang],
                    "suggestions": []}  # fmt: skip
        results = apply_batch(user, [(p, p.preview_hash) for p in previews], operation_id,
                              thread_id)  # fmt: skip
    except WriteError as exc:
        return {"write": {**write, "status": "error"}, "answer": exc.message, "suggestions": []}
    events = [event for result in results for event in result.events]
    return {"write": {**write, "status": "done", "results": [r.model_dump() for r in results]},
            "write_events": events, "answer": " ".join(r.message for r in results),
            "suggestions": []}  # fmt: skip


# ------------------------------------------------------------------ routing
def after_plan(state: AgentState) -> Literal["check_write", "finalize"]:
    return "check_write" if _w(state).get("status") == "checking" else "finalize"


def after_check(state: AgentState) -> Literal["ask_write", "confirm_write", "finalize"]:
    status = _w(state)["status"]
    if status == "question":
        return "ask_write"
    return "confirm_write" if status == "ready" else "finalize"


def after_ask(state: AgentState) -> Literal["check_write", "ask_write", "finalize"]:
    status = _w(state)["status"]
    if status == "checking":
        return "check_write"
    return "ask_write" if status == "question" else "finalize"
