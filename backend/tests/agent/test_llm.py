"""Model factory and budget guard (CLAUDE.md §4a). No network calls are made here."""

import pytest
from langchain_core.messages import HumanMessage

from app.agent import llm
from app.agent.state import GuardOutput
from app.core import config


def openai_mode(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    monkeypatch.setenv("LLM_MODE", "openai")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config.get_settings.cache_clear()


def test_fake_mode_never_builds_a_real_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: object, **__: object) -> None:
        raise AssertionError("ChatOpenAI must not be constructed in fake mode")

    monkeypatch.setattr(llm, "ChatOpenAI", boom)
    out = llm.structured_call(
        "guard", "fast", GuardOutput, [HumanMessage("<user_message>hi</user_message>")]
    )
    assert out.intent == "greeting"


def test_openai_mode_requires_key_and_pinned_models(monkeypatch: pytest.MonkeyPatch) -> None:
    openai_mode(monkeypatch, OPENAI_API_KEY="sk-test-not-real", OPENAI_MODEL_FAST="")
    with pytest.raises(llm.LLMConfigError):
        llm.chat_model("fast", "guard")


def test_chat_model_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    openai_mode(
        monkeypatch,
        OPENAI_API_KEY="sk-test-not-real",
        OPENAI_MODEL_FAST="gpt-4.1-mini-2025-04-14",
        OPENAI_MODEL_REASONING="gpt-4.1-2025-04-14",
    )
    fast = llm.chat_model("fast", "guard")
    assert fast.model_name == "gpt-4.1-mini-2025-04-14"
    assert fast.temperature == 0
    assert fast.max_tokens == llm.MAX_TOKENS["guard"]
    assert llm.chat_model("reasoning", "sql").model_name == "gpt-4.1-2025-04-14"


def test_budget_stops_calls_before_any_network(monkeypatch: pytest.MonkeyPatch) -> None:
    openai_mode(
        monkeypatch, OPENAI_API_KEY="sk-test-not-real", OPENAI_MODEL_FAST="gpt-4.1-mini",
        OPENAI_DAILY_TOKEN_BUDGET="1000",  # noqa: S106 (a number, not a secret)
    )  # fmt: skip
    monkeypatch.setattr(llm, "chat_model", lambda *_: pytest.fail("network path reached"))
    llm.budget.reset()
    llm.budget.add(1000)
    try:
        with pytest.raises(llm.LLMBudgetExceededError):
            llm.structured_call("guard", "fast", GuardOutput, [HumanMessage("x")])
    finally:
        llm.budget.reset()


def test_budget_counts_and_resets() -> None:
    llm.budget.reset()
    llm.budget.add(150)
    llm.budget.add(-5)  # never negative
    assert llm.budget.used == 150
    llm.budget.reset()
    assert llm.budget.used == 0
