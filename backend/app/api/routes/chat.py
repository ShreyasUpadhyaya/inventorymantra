"""Chat API (C4.5): streamed turns over Server-Sent Events, plus history.

POST /chat                      {message, thread_id?} -> SSE: status* then answer|clarify|error
POST /chat/{thread_id}/resume   {answer}              -> SSE (answers a clarifying question)
GET  /chat/threads              the caller's conversations
GET  /chat/threads/{thread_id}  messages of one of the caller's conversations

The graph runs in a worker thread (LangSmith tracing context is thread-bound); steps reach
the client through a queue. Another user's thread answers 404, exactly like a missing one.
"""

import json
import logging
import queue
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agent.run import ThreadAccessError, TurnResult, pending_question, resume_turn, run_turn
from app.api.deps import Conn, CurrentUser
from app.auth.service import AuthUser
from app.core.config import get_settings
from app.db.pool import get_pool
from app.services import chat_store
from app.services.chat_store import MessageOut, ThreadOut

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger("inventorymantra.chat")

MAX_MESSAGE = 2000
STEP_LABELS = {
    "guard": "Understanding your question",
    "resolve_entities": "Finding what you mentioned",
    "clarify_check": "Checking the details",
    "generate_sql": "Writing the query",
    "execute_sql": "Reading the data",
    "compose": "Preparing the answer",
}
SAFE_ERROR = "Something went wrong while answering. Please try again."


class ChatIn(BaseModel):
    # Swagger example without thread_id: its placeholder UUID would point at no conversation.
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={"examples": [{"message": "stock of bush"}]},
    )

    message: str = Field(min_length=1, max_length=MAX_MESSAGE)
    thread_id: UUID | None = None


class ResumeIn(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={"examples": [{"answer": "OD42"}]},
    )

    answer: str = Field(min_length=1, max_length=200)


class _PerUserLimit:
    def __init__(self) -> None:
        self._hits: dict[UUID, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, user_id: UUID) -> bool:
        now, limit = time.monotonic(), get_settings().chat_messages_per_minute
        with self._lock:
            hits = self._hits[user_id]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


chat_limit = _PerUserLimit()


def _event(kind: str, data: dict[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _final_event(result: TurnResult) -> str:
    body = result.model_dump(exclude={"entity_mentions", "answer_checks", "resolved"})
    return _event("clarify" if result.interrupt else "answer", body)


def _stream(
    user: AuthUser, thread_id: UUID, user_text: str, work: Callable[..., TurnResult]
) -> Iterator[str]:
    """Run `work(on_step=...)` in a thread; stream its steps and the final result."""
    events: queue.Queue[tuple[str, Any]] = queue.Queue()

    def target() -> None:
        try:
            result = work(on_step=lambda node: events.put(("step", node)))
            with get_pool().connection() as conn:
                chat_store.add_message(conn, thread_id, "user", user_text)
                chat_store.add_message(conn, thread_id, "assistant", result.answer,
                                       chat_store.assistant_payload(result))  # fmt: skip
            events.put(("done", result))
        except Exception:  # never leak internals to the client
            logger.exception("chat turn failed")
            events.put(("error", None))

    threading.Thread(target=target, daemon=True).start()
    yield _event("thread", {"thread_id": str(thread_id)})
    while True:
        kind, value = events.get()
        if kind == "step":
            if value in STEP_LABELS:
                yield _event("status", {"step": value, "label": STEP_LABELS[value]})
        elif kind == "done":
            yield _final_event(value)
            return
        else:
            yield _event("error", {"message": SAFE_ERROR})
            return


def _sse(body: Iterator[str]) -> StreamingResponse:
    return StreamingResponse(
        body,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("")
def chat(body: Annotated[ChatIn, Body()], user: CurrentUser, conn: Conn) -> StreamingResponse:
    if not chat_limit.allow(user.id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many messages. Please wait.")
    if body.thread_id is None:
        thread_id = chat_store.create_thread(conn, user.id, body.message)
        conn.commit()  # the worker thread writes messages on its own connection
    elif chat_store.owns_thread(conn, user.id, body.thread_id):
        thread_id = body.thread_id
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
    turns = chat_store.history(conn, thread_id)

    def work(on_step: Callable[[str], None]) -> TurnResult:
        return run_turn(user, body.message, history=turns, thread_id=str(thread_id),
                        on_step=on_step)  # fmt: skip

    return _sse(_stream(user, thread_id, body.message, work))


@router.post("/{thread_id}/resume")
def resume(
    thread_id: UUID, body: Annotated[ResumeIn, Body()], user: CurrentUser, conn: Conn
) -> StreamingResponse:
    if not chat_store.owns_thread(conn, user.id, thread_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
    if not chat_limit.allow(user.id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many messages. Please wait.")
    if pending_question(str(thread_id)) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No question is waiting for an answer.")

    def work(on_step: Callable[[str], None]) -> TurnResult:
        try:
            return resume_turn(user, str(thread_id), body.answer, on_step=on_step)
        except ThreadAccessError as exc:  # checked above; defence in depth
            raise RuntimeError("thread access") from exc

    return _sse(_stream(user, thread_id, body.answer, work))


@router.get("/threads", response_model=list[ThreadOut])
def threads(user: CurrentUser, conn: Conn) -> list[ThreadOut]:
    return chat_store.list_threads(conn, user.id)


@router.get("/threads/{thread_id}", response_model=list[MessageOut])
def thread_messages(thread_id: UUID, user: CurrentUser, conn: Conn) -> list[MessageOut]:
    found = chat_store.messages(conn, user.id, thread_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
    return found
