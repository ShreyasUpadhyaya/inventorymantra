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
    sql = _corpus_sql().get((role, _plain(latest_user_text(messages))))
    if sql:
        return {"sql": sql, "assumptions": ["offline mode: reviewed SQL for a known question"],
                "answerable": True}  # fmt: skip
    return {"sql": None, "assumptions": ["offline mode answers only known evaluation questions"],
            "answerable": False}  # fmt: skip


_DEFAULTS: dict[str, Handler] = {"guard": fake_guard, "sql": fake_sql}
