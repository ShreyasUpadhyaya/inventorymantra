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
    r"how are we doing|how are things|"
    # "how is the business going?", "how's our company doing" (voice test 2026-10-05)
    r"how(?:'s| is| are)? (?:the |our )?(?:business|company)\b|"
    r"how(?:'s| is| are) (?:it|everything|things) (?:going|doing)|"
    r"overall (?:status|picture|performance)|performance overview|kya haal|"
    r"kais[aei] (?:chal rah[aei]|kar rah[ei])|"  # Hinglish: kaisa chal raha, kaise kar rahe
    r"कैस[ाेी] (?:चल रह[ाेी]|कर रह[ेी])|क्या हाल",  # user test 2026-10-04: हम कैसा कर रहे हैं?
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


# A whole message that is only a greeting or thanks (probe 2026-10-04: "Thanks!" was sent to
# the SQL step and came back "not recorded").
GREETING = re.compile(
    r"^\s*(hi|hello|hey|namaste|namaskar|नमस्ते|good (?:morning|afternoon|evening))[\s!.]*$",
    re.I,
)
THANKS = re.compile(
    r"^\s*(thanks?|thank you|thx|ok(?:ay)?,? thanks?|dhanyavaad|dhanyawad|shukriya|धन्यवाद|"
    r"शुक्रिया)[\s!.]*$",
    re.I,
)
# A change request: a write verb first and a number ("Add 3 to 132 and 5 to the other" was
# read as a question when no item was named; probe 2026-10-04).
WRITE_START = re.compile(
    r"^\s*(add|issue|transfer|move|remove|receive|record|request|raise|place|create)\b.*\d",
    re.I,
)
_CLAUSE_SPLIT = re.compile(r"\s*(?:\band then\b|\bthen\b|\band\b|\baur\b|और|फिर|[,;/])\s*", re.I)


def without_off_topic(message: str) -> str:
    """The message minus its clearly off-topic clauses ("stock of bush and write a poem" ->
    "stock of bush"), so the composer never sees the poem request (probe 2026-10-04)."""
    clauses = [c for c in _CLAUSE_SPLIT.split(message) if c and c.strip()]
    kept = [c for c in clauses if not OFF_TOPIC.search(c)]
    return ", ".join(kept) if kept and len(kept) < len(clauses) else message


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
