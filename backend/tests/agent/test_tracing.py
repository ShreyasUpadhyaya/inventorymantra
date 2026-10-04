"""LangSmith wiring without touching the network: tags reach the trace, and tracing turns
on only when the app enables it with a key."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from app.agent import run as run_module
from app.agent import tracing
from app.core import config
from tests.agent.conftest import make_user


class FakeRun:
    def __init__(self) -> None:
        self.tags: list[str] = []
        self.outputs: dict[str, Any] = {}

    def add_tags(self, tags: list[str]) -> None:
        self.tags.extend(tags)

    def end(self, outputs: dict[str, Any]) -> None:
        self.outputs = outputs


def test_turn_is_traced_with_role_lang_route_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict[str, Any] = {}

    @contextmanager
    def fake_trace(name: str, **kwargs: Any) -> Iterator[FakeRun]:
        recorded.update(name=name, **kwargs)
        recorded["run"] = FakeRun()
        yield recorded["run"]

    monkeypatch.setattr(run_module, "tracing_enabled", lambda: True)
    monkeypatch.setattr(run_module.langsmith, "trace", fake_trace)
    user = make_user("inventory_manager")
    run_module.run_turn(user, "What price did we pay for SS sheet?")

    assert recorded["name"] == "chat_turn"
    assert recorded["tags"] == ["role:inventory_manager"]
    assert recorded["run"].tags == ["role:inventory_manager", "lang:en", "route:refuse_rbac",
                                    "rbac_denied"]  # fmt: skip
    metadata = recorded["metadata"]
    assert metadata["prompt_version"] == "0.9" and metadata["llm_mode"] == "fake"
    assert str(user.id) not in str(metadata) and user.email not in str(metadata)
    assert recorded["run"].outputs["route"] == "refuse_rbac"


def test_tracing_stays_off_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "")
    config.get_settings.cache_clear()
    assert tracing.configure_tracing() is False
    assert tracing.tracing_enabled() is False


def test_tracing_turns_on_with_flag_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test_not_real")
    monkeypatch.setenv("LANGSMITH_PROJECT", "unit-test-project")
    config.get_settings.cache_clear()
    assert tracing.configure_tracing() is True
    assert tracing.tracing_enabled() is True


def test_user_ref_is_stable_and_not_reversible() -> None:
    assert tracing.user_ref("abc") == tracing.user_ref("abc")
    assert tracing.user_ref("abc") != "abc" and len(tracing.user_ref("abc")) == 12


def test_paused_turn_is_traced_as_clarify(monkeypatch: pytest.MonkeyPatch, db: object) -> None:
    recorded: dict[str, Any] = {}

    @contextmanager
    def fake_trace(name: str, **kwargs: Any) -> Iterator[FakeRun]:
        recorded["run"] = FakeRun()
        yield recorded["run"]

    from app.core.config import get_settings

    if get_settings().readonly_db_password is None:
        pytest.skip("READONLY_DB_PASSWORD not set")
    monkeypatch.setattr(run_module, "tracing_enabled", lambda: True)
    monkeypatch.setattr(run_module.langsmith, "trace", fake_trace)
    run_module.run_turn(make_user("procurement_manager"), "What did we spend earlier?")
    assert "route:clarify" in recorded["run"].tags
    assert recorded["run"].outputs == {"answer": "", "paused": True, "route": "clarify"}
