"""Conversation history (C4.5). Every lookup is scoped to the owner: another user's thread is
indistinguishable from one that doesn't exist."""

import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from app.agent.state import Turn

TITLE_LENGTH = 80
STORED_ROWS = 200  # rows kept with a message for re-display; the live answer has up to 500


class ThreadOut(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    id: UUID
    role: str
    content: str
    payload: dict[str, Any]
    created_at: datetime


def create_thread(conn: psycopg.Connection, user_id: UUID, first_message: str) -> UUID:
    thread_id = uuid.uuid4()
    title = " ".join(first_message.split())[:TITLE_LENGTH] or "New conversation"
    conn.execute(
        "INSERT INTO chat_threads (id, user_id, title) VALUES (%s, %s, %s)",
        (thread_id, user_id, title),
    )
    return thread_id


def owns_thread(conn: psycopg.Connection, user_id: UUID, thread_id: UUID) -> bool:
    row = conn.execute(
        "SELECT 1 FROM chat_threads WHERE id = %s AND user_id = %s", (thread_id, user_id)
    ).fetchone()
    return row is not None


def add_message(
    conn: psycopg.Connection, thread_id: UUID, role: str, content: str,
    payload: dict[str, Any] | None = None,
) -> None:  # fmt: skip
    conn.execute(
        """INSERT INTO chat_messages (id, thread_id, role, content, payload)
           VALUES (%s, %s, %s, %s, %s)""",
        (uuid.uuid4(), thread_id, role, content, Jsonb(payload or {})),
    )
    conn.execute("UPDATE chat_threads SET updated_at = now() WHERE id = %s", (thread_id,))


def history(conn: psycopg.Connection, thread_id: UUID, limit: int = 6) -> list[Turn]:
    rows = conn.execute(
        """SELECT role, content FROM chat_messages WHERE thread_id = %s
           ORDER BY created_at DESC LIMIT %s""",
        (thread_id, limit),
    ).fetchall()
    return [{"role": role, "content": content} for role, content in reversed(rows)]


def list_threads(conn: psycopg.Connection, user_id: UUID) -> list[ThreadOut]:
    rows = conn.execute(
        """SELECT id, title, created_at, updated_at FROM chat_threads WHERE user_id = %s
           ORDER BY updated_at DESC LIMIT 50""",
        (user_id,),
    ).fetchall()
    return [ThreadOut(id=r[0], title=r[1], created_at=r[2], updated_at=r[3]) for r in rows]


def messages(conn: psycopg.Connection, user_id: UUID, thread_id: UUID) -> list[MessageOut] | None:
    if not owns_thread(conn, user_id, thread_id):
        return None
    rows = conn.execute(
        """SELECT id, role, content, payload, created_at FROM chat_messages
           WHERE thread_id = %s ORDER BY created_at""",
        (thread_id,),
    ).fetchall()
    return [
        MessageOut(id=r[0], role=r[1], content=r[2], payload=r[3], created_at=r[4]) for r in rows
    ]


def assistant_payload(result: Any) -> dict[str, Any]:
    """What the UI needs to redraw an answer later (rows trimmed)."""
    return {
        "route": result.route,
        "lang": result.lang,
        "suggestions": result.suggestions,
        "interrupt": result.interrupt,
        "viz": result.viz,
        "columns": result.columns,
        "rows": result.rows[:STORED_ROWS],
        "row_count": result.row_count,
        "denied_areas": result.denied_areas,
        "failure": result.failure,
        "sql": result.sql,
    }
