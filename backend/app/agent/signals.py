"""Deterministic signals that back up model judgements (smoke-test findings, D-036).

The real model (gpt-4.1-mini) over-asked for periods, missed off-topic parts and mislabelled
Roman-script Hindi. These checks are cheap, testable and identical online and offline.
"""

import re
from typing import Literal, cast

Lang = Literal["en", "hi", "hinglish"]
DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# Vague time wording: only then may the bot ask "which period?"
VAGUE_TIME = re.compile(
    r"a few days (?:back|ago)|few days ago|couple of days|some (?:days|time|while) (?:back|ago)|"
    r"a while (?:back|ago)|the other day|\brecent(?:ly)?\b|\blately\b|\bearlier\b|"
    r"past few (?:days|weeks)|last few (?:days|weeks)|thode din pehle|kuch din pehle|"
    r"kuch samay pehle|haal hi mein|pichle kuch din|pichle dino|"
    r"कुछ दिन पहले|हाल ही में|पिछले कुछ दिन|कुछ समय पहले",
    re.I,
)

# Vague measures: only then may the bot ask "what would you like to see?"
VAGUE_MEASURE = re.compile(
    r"how are we doing|how(?:'s| is) (?:business|it going|everything)|overall (?:status|picture)|"
    r"performance overview|kaisa chal raha|kya haal|कैसा चल रहा|क्या हाल",
    re.I,
)

# Clearly unrelated requests: the out-of-scope sentence is always added for these.
OFF_TOPIC = re.compile(
    r"\b(poem|poetry|story|joke|song|lyrics|recipe|weather|horoscope|shayari|essay|"
    r"stock market|sensex|nifty|cricket|football|ipl|movie|film|bitcoin|crypto|election|"
    r"net worth|richest|celebrity|prime minister|president of|capital of)\b|"
    r"कविता|कहानी|चुटकुला|गाना|मौसम|क्रिकेट|फिल्म|संपत्ति",
    re.I,
)


# Words that name a data area outright. When the model calls such a request off-topic, the
# role decides: allowed -> answer it, not allowed -> the fixed denial (full eval run 1).
AREA_WORDS: list[tuple[str, re.Pattern[str]]] = [
    ("users", re.compile(r"\b(users?|user accounts?|logins?|who has access)\b", re.I)),
    ("vendors", re.compile(r"\b(vendors?|suppliers?)\b", re.I)),
    ("prices", re.compile(r"\b(prices?|unit costs?|rates)\b", re.I)),
    ("margins", re.compile(r"\b(margins?|profits?)\b", re.I)),
    ("sales", re.compile(r"\b(sales|revenue|customers?)\b", re.I)),
    ("audit_all", re.compile(r"\baudit\b", re.I)),
]


def areas_named(message: str) -> list[str]:
    return [area for area, pattern in AREA_WORDS if pattern.search(message)]


def script_language(declared: str, message: str) -> Lang:
    """Hindi is Devanagari; Hindi words in Roman letters are Hinglish (D-036)."""
    has_devanagari = bool(DEVANAGARI.search(message))
    if declared == "hi" and not has_devanagari:
        return "hinglish"
    if declared == "hinglish" and has_devanagari:
        return "hi"
    return cast(Lang, declared)


# "Will it...?" questions: the answer says it can't predict, then gives the recorded facts (D-040).
FUTURE_QUESTION = re.compile(
    r"\b(will|won't|going to|run out|kal tak|aa jayega|aayega|jayega|kab tak)\b|"
    r"आएगा|जाएगा|होगा|कल तक|कब तक",
    re.I,
)

# A question that asks for a figure (shown as a KPI card, not a chart).
FIGURE_QUESTION = re.compile(
    r"\b(how many|how much|total|count|number of|average|sum|kitne|kitna|kitni|kul)\b|"
    r"कितने|कितना|कितनी|कुल|औसत",
    re.I,
)
