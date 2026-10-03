"""No-prediction check (CLAUDE.md §6; DECISION D-010).

Two layers, cheapest first:
1. banned phrases (en / hi / Hinglish): a hit fails immediately, no model call;
2. future-ish wording that *might* be a prediction ("will", "next", "expected to") triggers the
   judge model (PROMPTS.md §8); plain factual answers never pay for a judge call.
"""

import re

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict

from app.agent.llm import structured_call
from app.agent.prompts.compose import JUDGE_SYSTEM

# Negative forms count too (smoke run 3: "is not expected to arrive on 1 Oct").
BANNED = re.compile(
    r"\b(will (probably|likely|arrive|run out|be delivered|reach|cost|increase|decrease)|"
    r"(will not|won't) (arrive|run out|be delivered|reach|last)|"
    r"(is|are)(n't| not)? (likely|unlikely|expected|going) to|unlikely to|"
    r"should(n't| not)? (arrive|last|be (delivered|received))|"
    r"probably|forecast|projected|predict\w*|in the coming (days|weeks)|run out (by|in|on)|"
    r"aa jayega|aa jaayega|nahi aayega|nahin aayega|shayad|ho jayega|ho sakta hai)\b|"
    r"शायद|आ जाएगा|नहीं आएगा|होने की संभावना|उम्मीद है कि|खत्म हो जाएगा|अनुमान",
    re.I,
)
FUTURE_HINT = re.compile(
    r"\b(will|next (week|month|quarter)|soon|upcoming|expected to|going to|kal tak|agle)\b|"
    r"अगले|जल्द",
    re.I,
)


class JudgeOutput(BaseModel):
    """PROMPTS.md §8 output. All fields required for OpenAI strict JSON schema."""

    model_config = ConfigDict(extra="forbid")

    predictive: bool
    span: str | None


def banned_span(answer: str) -> str | None:
    found = BANNED.search(answer)
    return found.group(0) if found else None


def check_prediction(answer: str) -> str | None:
    """The predictive phrase, or None if the answer only states facts."""
    span = banned_span(answer)
    if span or not FUTURE_HINT.search(answer):
        return span
    verdict = structured_call(
        "judge",
        "fast",
        JudgeOutput,
        [SystemMessage(JUDGE_SYSTEM), HumanMessage(f"<answer>{answer}</answer>")],
    )
    return (verdict.span or "predictive wording") if verdict.predictive else None
