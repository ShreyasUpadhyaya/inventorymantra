"""Owner-only user management: list, create and reset (temp password shown once), (de)activate."""

import re
import secrets
import uuid
from datetime import datetime
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.deps import Conn, require_action
from app.auth.passwords import hash_password
from app.auth.service import AuthUser, Role, normalise_email, revoke_all_sessions, set_password
from app.services import audit

router = APIRouter(prefix="/users", tags=["users"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CanManage = Annotated[AuthUser, Depends(require_action("create_user"))]
CanDeactivate = Annotated[AuthUser, Depends(require_action("deactivate_user"))]
CanReset = Annotated[AuthUser, Depends(require_action("reset_password"))]


class UserRow(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    created_at: datetime
    created_by_name: str | None


class CreateUserIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: str = Field(min_length=3, max_length=254)
    full_name: str = Field(min_length=1, max_length=120)
    role: Role

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        value = normalise_email(value)
        if not EMAIL_RE.match(value):
            raise ValueError("not a valid email address")
        return value


class CreatedUser(UserRow):
    temp_password: str = Field(description="Shown once. Ask the user to change it on first use.")


LIST_SQL = """
SELECT u.id, u.email, u.full_name, u.role, u.is_active, u.created_at,
       c.full_name AS created_by_name
FROM users u LEFT JOIN users c ON c.id = u.created_by
"""


def _get(conn: psycopg.Connection, user_id: UUID) -> UserRow | None:
    row = conn.execute(LIST_SQL + " WHERE u.id = %s", (user_id,)).fetchone()
    return UserRow(**dict(zip(UserRow.model_fields, row, strict=True))) if row else None


@router.get("", response_model=list[UserRow])
def list_users(_: CanManage, conn: Conn) -> list[UserRow]:
    rows = conn.execute(LIST_SQL + " ORDER BY u.is_active DESC, u.role, u.full_name").fetchall()
    return [UserRow(**dict(zip(UserRow.model_fields, r, strict=True))) for r in rows]


@router.post("", response_model=CreatedUser, status_code=status.HTTP_201_CREATED)
def create_user(body: Annotated[CreateUserIn, Body()], owner: CanManage, conn: Conn) -> CreatedUser:
    temp_password = secrets.token_urlsafe(12)  # generated server-side, never logged or stored
    user_id = uuid.uuid4()
    try:
        conn.execute(
            """INSERT INTO users (id, email, password_hash, full_name, role, created_by,
                                  must_change_password)
               VALUES (%s, %s, %s, %s, %s, %s, true)""",
            (
                user_id,
                body.email,
                hash_password(temp_password),
                body.full_name,
                body.role,
                owner.id,
            ),
        )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A user with this email already exists."
        ) from None
    audit.record(
        conn, owner, "create_user", "user", str(user_id), None,
        {"email": body.email, "full_name": body.full_name, "role": body.role},
    )  # fmt: skip
    created = _get(conn, user_id)
    assert created is not None
    return CreatedUser(**created.model_dump(), temp_password=temp_password)


def _set_active(conn: psycopg.Connection, owner: AuthUser, user_id: UUID, active: bool) -> UserRow:
    target = _get(conn, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    if user_id == owner.id and not active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't deactivate your own account.")
    conn.execute(
        "UPDATE users SET is_active = %s, updated_at = now() WHERE id = %s", (active, user_id)
    )
    if not active:
        revoke_all_sessions(conn, user_id)  # logged out everywhere, immediately
    audit.record(
        conn, owner, "deactivate_user" if not active else "activate_user", "user", str(user_id),
        {"is_active": target.is_active}, {"is_active": active},
    )  # fmt: skip
    updated = _get(conn, user_id)
    assert updated is not None
    return updated


@router.post("/{user_id}/deactivate", response_model=UserRow)
def deactivate(user_id: UUID, owner: CanDeactivate, conn: Conn) -> UserRow:
    return _set_active(conn, owner, user_id, active=False)


@router.post("/{user_id}/activate", response_model=UserRow)
def activate(user_id: UUID, owner: CanDeactivate, conn: Conn) -> UserRow:
    return _set_active(conn, owner, user_id, active=True)


class ResetPassword(BaseModel):
    user: UserRow
    temp_password: str = Field(description="Shown once. The user must change it at next login.")


@router.post("/{user_id}/reset-password", response_model=ResetPassword)
def reset_password(user_id: UUID, owner: CanReset, conn: Conn) -> ResetPassword:
    """A forgotten password: new temporary password, signed out everywhere (C5.1b)."""
    target = _get(conn, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    if user_id == owner.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Change your own password from your account page."
        )
    temp_password = secrets.token_urlsafe(12)  # generated server-side, never logged or stored
    set_password(conn, user_id, temp_password, must_change=True)
    audit.record(conn, owner, "reset_password", "user", str(user_id), None,
                 {"must_change_password": True})  # fmt: skip
    return ResetPassword(user=target, temp_password=temp_password)
