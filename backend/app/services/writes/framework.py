"""Write framework (docs/WRITE_PATH.md §1–2): preview → confirm → apply, all-or-nothing.

Every write is an `Action`: it resolves the user's words into a canonical payload (`plan`),
reads the rows it will change (`read_state`, locked at apply time) and performs the change
(`apply`). This module wraps every action in the same guards:

  authorisation   policy.can(role, action) at preview AND at apply; the role is re-read
                  from the database at apply, never taken from chat text
  previewed=done  preview_hash = SHA-256(action, payload, user); apply refuses any other
  stale preview   state_version = hash of the locked rows; changed → abort, re-preview;
                  previews expire after 10 minutes
  double submit   operation_id is UNIQUE in agent_audit_log; a second confirm replays
                  the first result
  concurrency     the action locks its rows with SELECT ... FOR UPDATE in a fixed order
  audit           pending → success | failed | cancelled, before/after state, one row
                  per attempt; a failure is written in its own transaction after rollback

Writes never go through LLM SQL. The tool adapter (C6.3) and dashboard buttons call
`preview` / `apply` here, so there is one code path.
"""

import hashlib
import json
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Literal

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, ValidationError

from app.auth.service import AuthUser, load_user
from app.db.session import psycopg_dsn
from app.policy import permissions

logger = logging.getLogger("inventorymantra.writes")

PREVIEW_TTL = timedelta(minutes=10)
MAX_QUANTITY = 100_000
NOT_PERMITTED = "That action isn't available for your role."
UNEXPECTED = "No changes were made. Something went wrong while saving; please try again."


# ------------------------------------------------------------------ errors (safe to show)
class WriteError(Exception):
    """A refusal the user may read: no table names, no internals."""

    code: ClassVar[str] = "invalid"

    def __init__(
        self,
        message: str,
        options: list[str] | None = None,
        *,
        field: str | None = None,
        values: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.options = options or []
        self.field = field  # the argument this is about, e.g. "item" or "lines.0.item"
        self.values = values or list(self.options)  # what to use when an option is picked


class NotPermitted(WriteError):
    code = "not_permitted"

    def __init__(self, message: str = NOT_PERMITTED) -> None:
        super().__init__(message)


class Ambiguous(WriteError):
    code = "ambiguous"


class NotFound(WriteError):
    code = "not_found"


class PreviewMismatch(WriteError):
    code = "preview_mismatch"


class PreviewExpired(WriteError):
    code = "preview_expired"


class StalePreview(WriteError):
    code = "stale"


# ------------------------------------------------------------------ models
class PreviewField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value: str


class Preview(BaseModel):
    """What the user confirms. Stored server-side (graph state); only the hash comes back."""

    model_config = ConfigDict(extra="forbid")

    action: str
    user_id: str
    summary: str
    fields: list[PreviewField]
    effects: list[str]
    payload: dict[str, Any]
    preview_hash: str
    state_version: str
    expires_at: datetime


class WriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "awaiting_approval"]
    message: str
    operation_id: str
    entity_type: str
    entity_id: str | None
    events: list[dict[str, Any]] = []  # role-scoped notifications are built from these (C6.5)
    replayed: bool = False


@dataclass
class Plan:
    payload: dict[str, Any]  # canonical, resolved: ids and numbers only, JSON-safe
    summary: str
    fields: list[tuple[str, str]]
    effects: list[str]


@dataclass
class Outcome:
    message: str
    entity_id: str | None
    after: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)
    status: Literal["success", "awaiting_approval"] = "success"


class Action(ABC):
    """One write. `name` is the policy action (app/policy/permissions.py ACTIONS)."""

    name: ClassVar[str]
    entity_type: ClassVar[str]
    args_model: ClassVar[type[BaseModel]]

    @abstractmethod
    def plan(self, conn: psycopg.Connection, user: AuthUser, args: Any) -> Plan: ...

    @abstractmethod
    def read_state(
        self, conn: psycopg.Connection, payload: dict[str, Any], lock: bool
    ) -> dict[str, Any]:
        """The rows this action will change, in a JSON-safe form. With lock=True, take
        SELECT ... FOR UPDATE locks in a fixed order (by id) so writers can't deadlock."""

    @abstractmethod
    def apply(
        self,
        conn: psycopg.Connection,
        user: AuthUser,
        payload: dict[str, Any],
        state: dict[str, Any],
    ) -> Outcome: ...


REGISTRY: dict[str, Action] = {}


def register(action: Action) -> Action:
    REGISTRY[action.name] = action
    return action


# ------------------------------------------------------------------ helpers
def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def preview_hash(action: str, payload: dict[str, Any], user_id: str) -> str:
    body = _canonical({"action": action, "payload": payload, "user": user_id})
    return hashlib.sha256(body.encode()).hexdigest()


def state_version(state: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(state).encode()).hexdigest()


def check_quantity(quantity: float, unit: str | None = None) -> float:
    if quantity <= 0:
        raise WriteError("The quantity must be more than zero.")
    if quantity > MAX_QUANTITY:
        raise WriteError(f"The quantity can be at most {MAX_QUANTITY:,} per line.")
    return float(quantity)


def _connect() -> psycopg.Connection:
    """A dedicated autocommit connection, so `conn.transaction()` is a real transaction and a
    failure row can be committed after the rollback."""
    return psycopg.connect(psycopg_dsn(), autocommit=True, connect_timeout=5)


def _action(name: str) -> Action:
    if name not in REGISTRY:
        raise WriteError("I can't do that action.")
    return REGISTRY[name]


def _args_error(exc: ValidationError) -> WriteError:
    names = sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]})
    return WriteError("Some details are missing or invalid: " + ", ".join(names) + ".")


# ------------------------------------------------------------------ preview
def preview(user: AuthUser, action_name: str, raw_args: dict[str, Any]) -> Preview:
    """Resolve, validate and describe the change. Writes nothing."""
    action = _action(action_name)
    if not permissions.can(user.role, action.name):
        raise NotPermitted
    try:
        args = action.args_model.model_validate(raw_args)
    except ValidationError as exc:
        raise _args_error(exc) from None
    with _connect() as conn, conn.transaction(force_rollback=True):
        plan = action.plan(conn, user, args)
        state = action.read_state(conn, plan.payload, lock=False)
    user_id = str(user.id)
    return Preview(
        action=action.name,
        user_id=user_id,
        summary=plan.summary,
        fields=[PreviewField(label=k, value=v) for k, v in plan.fields],
        effects=plan.effects,
        payload=plan.payload,
        preview_hash=preview_hash(action.name, plan.payload, user_id),
        state_version=state_version(state),
        expires_at=datetime.now(UTC) + PREVIEW_TTL,  # wall clock, not the business date
    )


# ------------------------------------------------------------------ apply
def _replay(row: tuple[Any, ...]) -> WriteResult:
    status, after, error = row
    if status in ("success", "awaiting_approval") and after and "_result" in after:
        return WriteResult(**{**after["_result"], "replayed": True})
    if status == "cancelled":
        raise WriteError("This action was cancelled, so nothing was changed.")
    raise WriteError(error or UNEXPECTED)


def _existing(conn: psycopg.Connection, operation_id: str) -> tuple[Any, ...] | None:
    return conn.execute(
        "SELECT status, after_state, error FROM agent_audit_log WHERE operation_id = %s",
        (operation_id,),
    ).fetchone()


def _record(
    conn: psycopg.Connection,
    user: AuthUser,
    preview_: Preview,
    operation_id: str,
    thread_id: str | None,
    status: str,
    error: str | None = None,
) -> None:
    """Audit row for an attempt that didn't succeed (own transaction, after any rollback)."""
    action = REGISTRY.get(preview_.action)
    conn.execute(
        """INSERT INTO agent_audit_log (id, operation_id, user_id, role, operation, entity_type,
               before_state, status, error, thread_id, completed_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
           ON CONFLICT (operation_id) DO NOTHING""",
        (uuid.uuid4(), operation_id, user.id, user.role, preview_.action,
         action.entity_type if action else None, Jsonb({"payload": preview_.payload}),
         status, error, thread_id),
    )  # fmt: skip


def _apply_in_transaction(
    conn: psycopg.Connection,
    user: AuthUser,
    action: Action,
    preview_: Preview,
    confirmed_hash: str,
    operation_id: str,
    thread_id: str | None,
) -> WriteResult:
    fresh = load_user(conn, user.id)  # role and status from the DB, not from the session copy
    if (
        fresh is None
        or not fresh.is_active
        or fresh.role != user.role
        or not permissions.can(fresh.role, action.name)
    ):
        raise NotPermitted
    expected = preview_hash(action.name, preview_.payload, str(fresh.id))
    if preview_.user_id != str(fresh.id) or not (
        preview_.preview_hash == expected == confirmed_hash
    ):
        raise PreviewMismatch("That confirmation doesn't match the preview, so nothing changed.")
    if datetime.now(UTC) > preview_.expires_at:
        raise PreviewExpired("That preview has expired. Please ask again for a fresh one.")

    audit_id = uuid.uuid4()
    conn.execute(
        """INSERT INTO agent_audit_log (id, operation_id, user_id, role, operation, entity_type,
               status, thread_id) VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s)""",
        (audit_id, operation_id, fresh.id, fresh.role, action.name, action.entity_type,
         thread_id),
    )  # fmt: skip
    state = action.read_state(conn, preview_.payload, lock=True)
    if state_version(state) != preview_.state_version:
        raise StalePreview(
            "The stock or record changed since this preview, so nothing was saved. "
            "Please ask again to see the current figures."
        )
    outcome = action.apply(conn, fresh, preview_.payload, state)
    result = WriteResult(
        status=outcome.status, message=outcome.message, operation_id=operation_id,
        entity_type=action.entity_type, entity_id=outcome.entity_id, events=outcome.events,
    )  # fmt: skip
    conn.execute(
        """UPDATE agent_audit_log SET status = %s, entity_id = %s, before_state = %s,
               after_state = %s, completed_at = now() WHERE id = %s""",
        (outcome.status, outcome.entity_id, Jsonb(state),
         Jsonb({**outcome.after, "_result": result.model_dump(exclude={"replayed"})}),
         audit_id),
    )  # fmt: skip
    return result


def operation_ids(operation_id: str, count: int) -> list[str]:
    """One audit row per action: a single action keeps the id, several get #1, #2, …"""
    return [operation_id] if count == 1 else [f"{operation_id}#{i}" for i in range(1, count + 1)]


def apply_batch(
    user: AuthUser,
    confirmed: list[tuple[Preview, str]],
    operation_id: str,
    thread_id: str | None = None,
) -> list[WriteResult]:
    """Apply confirmed previews in ONE transaction (WRITE_PATH §5: "add 3 here and 5 there" is
    one confirmation and all-or-nothing). Safe to call twice with the same operation_id: the
    second call returns the first results and changes nothing."""
    if not confirmed:
        return []
    actions = [_action(p.action) for p, _ in confirmed]
    ids = operation_ids(operation_id, len(confirmed))
    with _connect() as conn:
        existing = [_existing(conn, i) for i in ids]
        if existing[0]:
            return [_replay(row) for row in existing if row]
        failure: str | None = None
        try:
            with conn.transaction():
                return [
                    _apply_in_transaction(conn, user, action, preview_, hash_, op_id, thread_id)
                    for action, (preview_, hash_), op_id in zip(
                        actions, confirmed, ids, strict=True
                    )
                ]
        except psycopg.errors.UniqueViolation:
            existing = [_existing(conn, i) for i in ids]  # a parallel confirm of the same card
            if existing[0]:
                return [_replay(row) for row in existing if row]
            logger.exception("write failed: %s", [a.name for a in actions])
            failure = UNEXPECTED
            raise WriteError(UNEXPECTED) from None
        except WriteError as exc:
            failure = exc.message
            raise
        except Exception:
            logger.exception("write failed: %s", [a.name for a in actions])  # stays in the log
            failure = UNEXPECTED
            raise WriteError(UNEXPECTED) from None
        finally:
            if failure is not None:
                for (preview_, _), op_id in zip(confirmed, ids, strict=True):
                    _record(conn, user, preview_, op_id, thread_id, "failed", failure)


def apply(
    user: AuthUser,
    preview_: Preview,
    confirmed_hash: str,
    operation_id: str,
    thread_id: str | None = None,
) -> WriteResult:
    """Apply one confirmed preview (see apply_batch)."""
    return apply_batch(user, [(preview_, confirmed_hash)], operation_id, thread_id)[0]


def cancel(
    user: AuthUser, previews: Preview | list[Preview], operation_id: str,
    thread_id: str | None = None,
) -> None:  # fmt: skip
    """The user declined the preview card: audited as cancelled, nothing else happens."""
    items = previews if isinstance(previews, list) else [previews]
    with _connect() as conn:
        for preview_, op_id in zip(items, operation_ids(operation_id, len(items)), strict=True):
            _record(conn, user, preview_, op_id, thread_id, "cancelled")
