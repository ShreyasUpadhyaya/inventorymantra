"""Chat API (C4.5): SSE turns, clarification over HTTP, history, ownership, limits,
and paused turns that survive a restart (Postgres checkpointer). Offline (LLM_MODE=fake)."""

import json
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import BaseMessage

from app.agent import fake
from app.agent.checkpoint import get_checkpointer
from app.agent.graph import build_graph
from app.api.routes.chat import SAFE_ERROR, chat_limit
from app.auth.tokens import create_access_token
from app.core import config
from app.main import app
from scripts.fabricate import demo_id

DEMO = {
    "inventory_manager": "inventory@demo.inventorymantra",
    "procurement_manager": "procurement@demo.inventorymantra",
    "owner": "owner@demo.inventorymantra",
}
IM, PM, OW = "inventory_manager", "procurement_manager", "owner"


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch, db: psycopg.Connection) -> Iterator[None]:
    monkeypatch.setenv("LLM_MODE", "fake")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("APP_TODAY", "2026-09-30")
    config.get_settings.cache_clear()
    settings = config.get_settings()
    if settings.readonly_db_password is None:
        pytest.skip("READONLY_DB_PASSWORD not set")
    try:
        settings.jwt_secret()
    except RuntimeError:
        pytest.skip("JWT_SECRET_KEY not set")
    chat_limit.clear()
    before = {r[0] for r in db.execute("SELECT id FROM chat_threads").fetchall()}
    yield
    created = [
        r[0] for r in db.execute("SELECT id FROM chat_threads").fetchall() if r[0] not in before
    ]
    for thread_id in created:
        get_checkpointer().delete_thread(str(thread_id))
    db.execute("DELETE FROM chat_threads WHERE id = ANY(%s)", (created,))
    config.get_settings.cache_clear()


def client_for(db: psycopg.Connection, role: str) -> TestClient:
    user_id = demo_id("user", DEMO[role])
    row = db.execute("SELECT token_version FROM users WHERE id = %s", (user_id,)).fetchone()
    assert row is not None, "run make seed"
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {create_access_token(user_id, row[0])}"
    return client


def events(client: TestClient, path: str, body: dict[str, Any]) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    with client.stream("POST", path, json=body) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        kind = None
        for line in response.iter_lines():
            if line.startswith("event: "):
                kind = line.removeprefix("event: ")
            elif line.startswith("data: "):
                out.append((kind, json.loads(line.removeprefix("data: "))))
    return out


def final(evts: list[tuple[str, Any]]) -> tuple[str, Any]:
    return evts[-1]


# ------------------------------------------------------------------ one streamed turn
def test_turn_streams_steps_then_the_answer(db: psycopg.Connection) -> None:
    evts = events(client_for(db, PM), "/chat", {"message": "Which purchase orders are overdue?"})
    kinds = [k for k, _ in evts]
    assert kinds[0] == "thread" and kinds[-1] == "answer"
    steps = [d["step"] for k, d in evts if k == "status"]
    assert steps == ["guard", "resolve_entities", "clarify_check", "generate_sql", "execute_sql",
                     "compose"]  # fmt: skip
    kind, answer = final(evts)
    assert answer["row_count"] == 28 and answer["viz"]["type"] == "table"
    assert "answer_checks" not in answer and "entity_mentions" not in answer


def test_history_is_saved_and_listed(db: psycopg.Connection) -> None:
    client = client_for(db, OW)
    evts = events(
        client, "/chat", {"message": "What were our profit margins for the last 3 quarters?"}
    )
    thread_id = evts[0][1]["thread_id"]
    listed = client.get("/chat/threads").json()
    assert listed[0]["id"] == thread_id
    assert listed[0]["title"] == "What were our profit margins for the last 3 quarters?"
    messages = client.get(f"/chat/threads/{thread_id}").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["payload"]["viz"]["type"] in ("line", "bar")
    assert len(messages[1]["payload"]["rows"]) == 3


def test_follow_up_questions_get_the_threads_history(db: psycopg.Connection) -> None:
    client = client_for(db, PM)
    first = events(client, "/chat", {"message": "Which purchase orders are overdue?"})
    thread_id = first[0][1]["thread_id"]
    seen: list[list[BaseMessage]] = []

    def guard(messages: list[BaseMessage]) -> Any:
        seen.append(messages)
        return fake.fake_guard(messages)

    with fake.scripted("guard", guard):
        events(
            client, "/chat", {"message": "and which vendor is the oldest?", "thread_id": thread_id}
        )
    contents = [str(m.content) for m in seen[0]]
    assert "<user_message>Which purchase orders are overdue?</user_message>" in contents
    assert contents[-1] == "<user_message>and which vendor is the oldest?</user_message>"


# ------------------------------------------------------------------ clarification over HTTP
def test_clarify_then_resume(db: psycopg.Connection) -> None:
    client = client_for(db, IM)
    asked = events(client, "/chat", {"message": "stock of bush"})
    kind, question = final(asked)
    assert kind == "clarify" and len(question["suggestions"]) == 2
    thread_id = asked[0][1]["thread_id"]
    done = events(client, f"/chat/{thread_id}/resume", {"answer": "OD42"})
    kind, answer = final(done)
    assert kind == "answer" and answer["rows"][0][0] == "BO-MC-0101"
    roles = [m["role"] for m in client.get(f"/chat/threads/{thread_id}").json()]
    assert roles == ["user", "assistant", "user", "assistant"]


def test_paused_question_survives_a_restart(db: psycopg.Connection) -> None:
    client = client_for(db, IM)
    asked = events(client, "/chat", {"message": "stock of bush"})
    thread_id = asked[0][1]["thread_id"]
    build_graph.cache_clear()  # simulate a new process: new graph, new checkpointer, new pool
    get_checkpointer.cache_clear()
    done = events(client, f"/chat/{thread_id}/resume", {"answer": "1"})
    assert final(done)[0] == "answer"


def test_resume_without_a_waiting_question_is_a_conflict(db: psycopg.Connection) -> None:
    client = client_for(db, PM)
    evts = events(client, "/chat", {"message": "Which purchase orders are overdue?"})
    r = client.post(f"/chat/{evts[0][1]['thread_id']}/resume", json={"answer": "1"})
    assert r.status_code == 409


# ------------------------------------------------------------------ ownership
def test_other_users_threads_look_missing(db: psycopg.Connection) -> None:
    owner = client_for(db, IM)
    asked = events(owner, "/chat", {"message": "stock of bush"})
    thread_id = asked[0][1]["thread_id"]
    intruder = client_for(db, PM)
    assert intruder.get(f"/chat/threads/{thread_id}").status_code == 404
    assert intruder.post(f"/chat/{thread_id}/resume", json={"answer": "1"}).status_code == 404
    hijack = intruder.post("/chat", json={"message": "hi", "thread_id": thread_id})
    assert hijack.status_code == 404
    assert all(t["id"] != thread_id for t in intruder.get("/chat/threads").json())
    unknown = "00000000-0000-0000-0000-000000000000"
    assert intruder.get(f"/chat/threads/{unknown}").status_code == 404


# ------------------------------------------------------------------ limits and failures
def test_requires_login() -> None:
    assert TestClient(app).post("/chat", json={"message": "hi"}).status_code == 401


@pytest.mark.parametrize(
    "body", [{"message": "x" * 2001}, {"message": ""}, {"message": "hi", "role": "owner"}]
)
def test_bad_bodies_are_rejected(db: psycopg.Connection, body: dict[str, Any]) -> None:
    assert client_for(db, PM).post("/chat", json=body).status_code == 422


def test_per_user_rate_limit(db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAT_MESSAGES_PER_MINUTE", "2")
    config.get_settings.cache_clear()
    client = client_for(db, OW)
    for _ in range(2):
        events(client, "/chat", {"message": "What can you do?"})
    assert client.post("/chat", json={"message": "What can you do?"}).status_code == 429


def test_internal_errors_are_reported_safely(db: psycopg.Connection) -> None:
    def broken(_: list[BaseMessage]) -> Any:
        raise RuntimeError("secret internal detail: password=hunter2")

    with fake.scripted("guard", broken):
        evts = events(client_for(db, PM), "/chat", {"message": "anything"})
    kind, data = final(evts)
    assert kind == "error" and data == {"message": SAFE_ERROR}
    assert "hunter2" not in json.dumps(evts)
