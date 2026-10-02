"""Offline stand-in for the language model (LLM_MODE=fake; CLAUDE.md §4a).

Each node has a deterministic, keyword-based handler, so the whole graph (routing,
denials, help, later clarify/SQL) can be clicked through with zero spend. It is NOT a
model of quality: real behaviour is measured in openai mode. Tests replace handlers with
exact answers via `scripted(...)`.
"""

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import BaseModel

Handler = Callable[[list[BaseMessage]], Any]

_overrides: dict[str, Handler] = {}


@contextmanager
def scripted(node: str, handler: Handler | BaseModel | dict[str, Any]) -> Iterator[None]:
    """Make `node` return a fixed answer (or call `handler`) while the context is open."""
    previous = _overrides.get(node)
    _overrides[node] = handler if callable(handler) else (lambda _msgs: handler)
    try:
        yield
    finally:
        if previous is None:
            _overrides.pop(node, None)
        else:
            _overrides[node] = previous


def respond[T: BaseModel](node: str, schema: type[T], messages: list[BaseMessage]) -> T:
    handler = _overrides.get(node) or _DEFAULTS.get(node)
    if handler is None:
        raise NotImplementedError(f"no fake handler for node {node!r}")
    out = handler(messages)
    return out if isinstance(out, schema) else schema.model_validate(out)


def latest_user_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            text = str(message.content)
            match = re.search(r"<user_message>(.*)</user_message>", text, re.S)
            return match.group(1) if match else text
    return ""


# ------------------------------------------------------------------ guard heuristics
DEVANAGARI = re.compile(r"[ऀ-ॿ]")
HINGLISH = re.compile(
    r"\b(kitne|kitna|kitni|hai|hain|kya|mein|batao|kar do|aur|ka|ke|ki|pichle|abhi|kal|"
    r"hamara|hamare|aa rahi|aa jayega|nikla|maal|sawaal|dikhao)\b",
    re.I,
)
OUT_OF_SCOPE = re.compile(
    r"\b(poem|weather|python|script|shares?|stock market|sensex|joke|translate|recipe)\b|कविता",
    re.I,
)
GREETING = re.compile(r"^\s*(hi|hello|hey|namaste|namaskar|नमस्ते)\b[\s!.]*$", re.I)
HELP = re.compile(
    r"what can (i|you)|help me|how do i use|system prompt|what tables|क्या पूछ|kya pooch", re.I
)
WRITE = re.compile(
    r"^\s*(add|issue|transfer|receive|record|request|raise|place|create|mark|cancel|approve|"
    r"reject|deactivate|set)\b|\b(add kar do|kar do|jod do)\b",
    re.I,
)
INJECTION = re.compile(
    r"ignore (all )?(previous|prior|your)|system prompt|i am the owner|you are now|"
    r"\b(select|drop|delete|insert|update)\b.+\b(from|table|into|set)\b",
    re.I,
)
AREAS: list[tuple[str, re.Pattern[str]]] = [
    ("prices", re.compile(r"\b(price|prices|cost|rate|paid for|uska price)\b|कीमत", re.I)),
    ("vendors", re.compile(r"\b(vendors?|suppliers?|proc_vendors)\b|सप्लायर", re.I)),
    ("margins", re.compile(r"\b(margins?|profit)\b|मार्जिन", re.I)),
    ("sales", re.compile(r"\b(revenue|sales|customers?)\b|कमाई|बिक्री", re.I)),
    ("users", re.compile(r"\b(users?|who has access)\b", re.I)),
    ("approvals", re.compile(r"\b(approv\w*)\b", re.I)),
    ("audit_all", re.compile(r"\baudit\b", re.I)),
    ("audit_own", re.compile(r"\bmy actions\b", re.I)),
    ("payments", re.compile(r"\b(payments?|instal+ments?|tranches?)\b", re.I)),
    ("spend", re.compile(r"\b(spend|spent)\b|खरीदारी", re.I)),
    ("purchase_orders", re.compile(r"\b(po|pos|purchase orders?|orders?)\b|पर्चेज", re.I)),
    ("reorder", re.compile(r"\b(reorder|run out|low stock)\b|मंगवाने", re.I)),
    ("requests", re.compile(r"\b(requests?|requested)\b", re.I)),
    ("inbound", re.compile(r"\b(coming in|arriving|inbound|aa rahi|expected)\b", re.I)),
    ("movements", re.compile(r"\b(issued|movements?|received|nikla)\b", re.I)),
    ("stock", re.compile(r"\b(stock|godown|warehouse)\b|स्टॉक", re.I)),
]
# Quantity words ("how many", "kitne") imply stock only when no other area is named:
# "how many vendors" is about vendors, not stock (fallback below).
ITEM_PHRASES = [  # where an item name usually sits in a question (offline stand-in only)
    re.compile(r"\bstock of (?:the )?(.+?)\s*\??$", re.I),
    re.compile(r"\bhow (?:much|many) (.+?) (?:do|have|are) we\b", re.I),
    re.compile(r"^(.+?) stock\b", re.I),
    re.compile(r"^(.+?) kitn[ei] hai", re.I),
    re.compile(r"\bcoming in for (.+?)\s*\??$", re.I),
    re.compile(r"^(.+?) kitni aa rahi", re.I),
]
VENDOR_PHRASE = re.compile(r"\b(?:from|with) ([a-z][\w.]*(?: [a-z][\w.]*){0,2})\s*\??$", re.I)
QUESTION_START = re.compile(
    r"^(which|what|what's|how|show|give|list|who|when|where|why|is|are|do|does|the|each|all)\b",
    re.I,
)
NOT_ITEMS = re.compile(r"^(items?|it|them|all|everything|maal|stuff)$", re.I)
MENTIONS: list[tuple[str, re.Pattern[str]]] = [
    ("po", re.compile(r"\bPO[\s-]*\d[\d-]*\b", re.I)),
    ("location", re.compile(r"\b(1\d\d-\d|C-?84)\b", re.I)),
    ("item", re.compile(r"\b[A-Z]{2}-[A-Z]{2}(?:-[A-Z]{2})?-\d{4}\b")),
]


def fake_guard(messages: list[BaseMessage]) -> dict[str, Any]:
    text = latest_user_text(messages)
    if DEVANAGARI.search(text):
        language = "hi"
    elif HINGLISH.search(text):
        language = "hinglish"
    else:
        language = "en"
    injection = bool(INJECTION.search(text))
    areas = [name for name, pattern in AREAS if pattern.search(text)]
    if "audit_own" in areas:
        areas = [a for a in areas if a != "audit_all"]
    if OUT_OF_SCOPE.search(text):
        intent = "out_of_scope"
    elif GREETING.search(text):
        intent = "greeting"
    elif HELP.search(text):
        intent = "help"
    elif WRITE.search(text):
        intent = "write"
    else:
        intent = "read"
    if intent in ("read", "write") and not areas:
        areas = ["stock"]
    keep_areas = intent in ("read", "write") or (intent == "out_of_scope" and areas)
    mentions = [
        {"type": kind, "text": m.group(0)}
        for kind, pattern in MENTIONS
        for m in pattern.finditer(text)
    ]
    short_statement = len(text.split()) <= 6 and not QUESTION_START.match(text.strip())
    if not any(m["type"] == "item" for m in mentions):
        for phrase_pattern in ITEM_PHRASES:
            if phrase_pattern.pattern.startswith("^") and not short_statement:
                continue  # "^X stock" only for short phrases like "hex nut m3 stock"
            found = phrase_pattern.search(text.strip())
            if found and not NOT_ITEMS.match(found.group(1).strip()):
                phrase = found.group(1).strip()
                if not any(phrase in m["text"] or m["text"] in phrase for m in mentions):
                    mentions.append({"type": "item", "text": phrase})
                break
    vendor = VENDOR_PHRASE.search(text.strip())
    if vendor and QUESTION_START.match(vendor.group(1)):
        vendor = None  # "from which vendor" names no vendor
    if vendor and {"purchase_orders", "vendors", "spend"} & set(areas):
        mentions.append({"type": "vendor", "text": vendor.group(1)})
    viz = re.search(r"\b(table|bar|line|pie)\b(?: chart)?", text, re.I)
    return {
        "language": language,
        "intent": intent,
        "data_areas": areas if keep_areas else [],
        "injection_suspected": injection,
        "explicit_viz": viz.group(1).lower() if viz else None,
        "parts": [text.strip()],
        "entity_mentions": mentions,
    }


# ------------------------------------------------------------------ SQL
ROLE_BY_LABEL = {
    "Inventory Manager": "inventory_manager",
    "Procurement Manager": "procurement_manager",
    "Owner": "owner",
}


def _plain(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.lower()).strip()


@lru_cache
def _corpus_sql() -> dict[tuple[str, str], str]:
    from evals.schema import load_corpus  # offline-only dependency on the eval corpus

    return {(c.role, _plain(c.query)): c.answer_key_sql for c in load_corpus() if c.answer_key_sql}


def fake_sql(messages: list[BaseMessage]) -> dict[str, Any]:
    """Offline mode can't write SQL from English: it answers only golden-corpus questions,
    with their reviewed answer-key SQL, and says so for anything else."""
    system = str(messages[0].content) if messages else ""
    label = re.search(r"whose role is: ([A-Za-z ]+)\.", system)
    role = ROLE_BY_LABEL.get(label.group(1).strip(), "") if label else ""
    message = latest_user_text(messages)
    sql = _corpus_sql().get((role, _plain(message)))
    if sql:
        return {"sql": sql, "assumptions": ["offline mode: reviewed SQL for a known question"],
                "answerable": True}  # fmt: skip
    sql = _entity_sql(role, message, _resolved(messages))
    if sql:
        return {"sql": sql, "assumptions": ["offline mode: template for the chosen record"],
                "answerable": True}  # fmt: skip
    return {"sql": None, "assumptions": [OFFLINE_UNSUPPORTED], "answerable": False}


OFFLINE_UNSUPPORTED = "offline mode answers only known evaluation questions"
INBOUND_WORDS = re.compile(r"coming in|arriv|inbound|expected|aa rah|on order", re.I)


def _resolved(messages: list[BaseMessage]) -> list[dict[str, str]]:
    import json

    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            found = re.search(r"<data>(.*?)</data>", str(message.content), re.S)
            if found:
                data: list[dict[str, str]] = json.loads(found.group(1) or "[]")
                return data
    return []


# Offline templates. Placeholders are filled only with codes the resolver read from the DB
# (quote-escaped), never with user text; the result still passes the SQL guard and runs as
# the role's read-only user.
TEMPLATES = {
    "item_inbound": "SELECT po_number, pending_qty, unit, expected_delivery_date "
    "FROM inbound_by_item WHERE item_code = '{item}' ORDER BY expected_delivery_date",
    "item_stock": "SELECT item_code, item_name, location_name, quantity, unit "
    "FROM stock_by_location WHERE item_code = '{item}'{location} ORDER BY quantity DESC",
    "po_status": "SELECT po_number, vendor_name, status, total_amount, expected_delivery_date, "
    "days_overdue FROM purchase_orders WHERE po_number = '{po}'",
    "po_inbound": "SELECT po_number, item_name, pending_qty, unit, expected_delivery_date "
    "FROM inbound_by_item WHERE po_number = '{po}'",
}


def _entity_sql(role: str, message: str, resolved: list[dict[str, str]]) -> str | None:
    """Simple questions about one chosen record, so the "which one?" flow completes offline."""
    codes = {r["type"]: r["code"].replace("'", "''") for r in resolved}
    if "item" in codes:
        if INBOUND_WORDS.search(message):
            return TEMPLATES["item_inbound"].format(item=codes["item"])
        location = f" AND location_name = '{codes['location']}'" if "location" in codes else ""
        return TEMPLATES["item_stock"].format(item=codes["item"], location=location)
    if "po" in codes:
        managers = role in ("procurement_manager", "owner")
        return TEMPLATES["po_status" if managers else "po_inbound"].format(po=codes["po"])
    return None


# ------------------------------------------------------------------ clarify
VAGUE_TIME = re.compile(
    r"a few days (?:back|ago)|few days ago|\brecent(?:ly)?\b|\bearlier\b|some time ago|"
    r"thode din pehle|kuch din pehle|haal hi mein|pichle dino|कुछ दिन पहले|हाल ही में",
    re.I,
)
EXPLICIT_PERIOD = re.compile(
    r"\b(today|yesterday|this|last|month|week|quarter|year|jan\w*|feb\w*|mar\w*|apr\w*|"
    r"may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*|20\d\d)\b",
    re.I,
)
VAGUE_MEASURE = re.compile(r"how are we doing|how is business|kaisa chal raha|कैसा चल रहा", re.I)
MEASURE_OPTIONS = {
    "inventory_manager": ["Stock levels", "Items to reorder", "Recent stock movements"],
    "procurement_manager": ["Procurement spend", "Overdue purchase orders", "Items to reorder"],
    "owner": ["Revenue and margins", "Procurement spend", "Overdue purchase orders"],
}
PERIOD_LABELS = {
    "en": ("Last 7 days", "Last 30 days", "This month"),
    "hi": ("पिछले 7 दिन", "पिछले 30 दिन", "इस महीने"),
    "hinglish": ("Pichle 7 din", "Pichle 30 din", "Is mahine"),
}
QUESTIONS = {
    "time_window": {"en": "Which period do you mean?", "hi": "आपका मतलब कौन सी अवधि से है?",
                    "hinglish": "Aapka matlab kaun si avadhi se hai?"},
    "measure": {"en": "What would you like to see?", "hi": "आप क्या देखना चाहेंगे?",
                "hinglish": "Aap kya dekhna chahenge?"},
}  # fmt: skip


def _lang(text: str) -> str:
    if DEVANAGARI.search(text):
        return "hi"
    return "hinglish" if HINGLISH.search(text) else "en"


def fake_clarify(messages: list[BaseMessage]) -> dict[str, Any]:
    from datetime import date, timedelta

    from app.core.config import app_today

    text = latest_user_text(messages)
    lang = _lang(text)
    no: dict[str, Any] = {"needs_clarification": False, "dimension": None, "question": None,
                          "options": [], "default_applied": None}  # fmt: skip
    if VAGUE_MEASURE.search(text):
        system = str(messages[0].content) if messages else ""
        label = re.search(r"whose role is: ([A-Za-z ]+)\.", system)
        role = ROLE_BY_LABEL.get(label.group(1).strip(), "owner") if label else "owner"
        question = QUESTIONS["measure"][lang]
        return {**no, "needs_clarification": True, "dimension": "measure",
                "question": question, "options": MEASURE_OPTIONS[role]}  # fmt: skip
    if VAGUE_TIME.search(text) and not EXPLICIT_PERIOD.search(text):
        today = app_today()

        def day(d: date) -> str:
            return f"{d.day} {d:%b}"

        def span(start: date) -> str:
            return f"{day(start)}–{day(today)} {today.year}"

        week, month, this = PERIOD_LABELS[lang]
        options = [f"{week} ({span(today - timedelta(days=6))})",
                   f"{month} ({span(today - timedelta(days=29))})",
                   f"{this} ({span(today.replace(day=1))})"]  # fmt: skip
        return {**no, "needs_clarification": True, "dimension": "time_window",
                "question": QUESTIONS["time_window"][lang], "options": options}  # fmt: skip
    return no


_DEFAULTS: dict[str, Handler] = {"guard": fake_guard, "sql": fake_sql, "clarify": fake_clarify}
