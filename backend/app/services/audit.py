"""Audit trail writes (agent_audit_log). The full write framework (preview hash, idempotent
operation ids, failure rows) arrives with WRITE_PATH.md in C6.1; this records direct API
actions in the same table so the Owner sees one history."""

import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.auth.service import AuthUser


def record(
    conn: psycopg.Connection,
    actor: AuthUser,
    operation: str,
    entity_type: str,
    entity_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    """Never pass secrets (passwords, tokens) in before/after."""
    conn.execute(
        """INSERT INTO agent_audit_log (id, operation_id, user_id, role, operation, entity_type,
               entity_id, before_state, after_state, status, completed_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'success', now())""",
        (
            uuid.uuid4(),
            f"api:{uuid.uuid4()}",
            actor.id,
            actor.role,
            operation,
            entity_type,
            entity_id,
            Jsonb(before) if before is not None else None,
            Jsonb(after) if after is not None else None,
        ),
    )
