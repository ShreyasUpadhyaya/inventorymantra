"""The one door to language models (CLAUDE.md §4a, budget rule).

`structured_call(node, tier, schema, messages)`:
- LLM_MODE=fake   -> offline stand-in from app.agent.fake (no network, no spend)
- LLM_MODE=openai -> ChatOpenAI at temperature 0 with the pinned model for the tier,
                     a per-node max_tokens cap, strict JSON-schema output, and a daily
                     token budget that refuses calls once spent.
"""

import threading
from datetime import date
from typing import Literal

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.agent import fake
from app.core.config import get_settings

Tier = Literal["fast", "reasoning"]

# Output-token caps per node; generous enough for the schema, small enough to bound cost.
MAX_TOKENS: dict[str, int] = {"guard": 400, "clarify": 300, "sql": 700}
DEFAULT_MAX_TOKENS = 800


class LLMBudgetExceededError(RuntimeError):
    """The daily token budget is used up; no more model calls today."""


class LLMConfigError(RuntimeError):
    """openai mode without a key or a pinned model name."""


class TokenBudget:
    """Process-local daily counter of input + output tokens."""

    def __init__(self) -> None:
        self._day = date.today()
        self._used = 0
        self._lock = threading.Lock()

    def _roll(self) -> None:
        if date.today() != self._day:
            self._day, self._used = date.today(), 0

    def check(self) -> None:
        with self._lock:
            self._roll()
            if self._used >= get_settings().openai_daily_token_budget:
                raise LLMBudgetExceededError("Daily OpenAI token budget reached.")

    def add(self, tokens: int) -> None:
        with self._lock:
            self._roll()
            self._used += max(tokens, 0)

    @property
    def used(self) -> int:
        return self._used

    def reset(self) -> None:
        with self._lock:
            self._day, self._used = date.today(), 0


budget = TokenBudget()


def chat_model(tier: Tier, node: str) -> ChatOpenAI:
    settings = get_settings()
    model = settings.openai_model_fast if tier == "fast" else settings.openai_model_reasoning
    if settings.openai_api_key is None or not model:
        raise LLMConfigError(
            "LLM_MODE=openai needs OPENAI_API_KEY and OPENAI_MODEL_FAST / OPENAI_MODEL_REASONING."
        )
    return ChatOpenAI(
        model=model,
        api_key=settings.openai_api_key,
        temperature=0,
        max_completion_tokens=MAX_TOKENS.get(node, DEFAULT_MAX_TOKENS),
        timeout=30,
        max_retries=1,
    )


def structured_call[T: BaseModel](
    node: str, tier: Tier, schema: type[T], messages: list[BaseMessage]
) -> T:
    if get_settings().llm_mode == "fake":
        return fake.respond(node, schema, messages)
    budget.check()
    runnable = chat_model(tier, node).with_structured_output(
        schema, method="json_schema", strict=True, include_raw=True
    )
    result = runnable.invoke(messages, config={"run_name": f"llm:{node}"})
    usage = getattr(result["raw"], "usage_metadata", None) or {}
    budget.add(int(usage.get("total_tokens", 0)))
    parsed = result["parsed"]
    if not isinstance(parsed, schema):
        raise ValueError(f"{node}: model output did not match {schema.__name__}")
    return parsed
