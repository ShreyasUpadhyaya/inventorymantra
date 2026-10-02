"""Authentication logic: credential checks, token issue/rotation/revocation, user loading."""

import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Literal
from uuid import UUID

import psycopg
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict

from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import create_access_token, hash_refresh_token, new_refresh_token
from app.core.config import get_settings

Role = Literal["owner", "procurement_manager", "inventory_manager"]


class AuthUser(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    token_version: int


class RefreshReuseError(Exception):
    """An already-rotated refresh token was presented: treat it as stolen."""


USER_COLUMNS = "id, email, full_name, role, is_active, token_version"


@lru_cache
def _dummy_hash() -> str:
    # Verifying against a dummy hash keeps unknown-email logins as slow as wrong passwords.
    return hash_password("dummy-password-for-timing")


def normalise_email(email: str) -> str:
    return email.strip().lower()


def load_user(conn: psycopg.Connection, user_id: UUID) -> AuthUser | None:
    with conn.cursor(row_factory=class_row(AuthUser)) as cur:
        return cur.execute(
            f"SELECT {USER_COLUMNS} FROM users WHERE id = %s",  # noqa: S608 (constant columns)
            (user_id,),
        ).fetchone()


def authenticate(conn: psycopg.Connection, email: str, password: str) -> AuthUser | None:
    row = conn.execute(
        f"SELECT {USER_COLUMNS}, password_hash FROM users WHERE email = %s",  # noqa: S608
        (normalise_email(email),),
    ).fetchone()
    if row is None:
        verify_password(password, _dummy_hash())
        return None
    *fields, password_hash = row
    user = AuthUser(**dict(zip(USER_COLUMNS.split(", "), fields, strict=True)))
    if not verify_password(password, password_hash) or not user.is_active:
        return None
    return user


def issue_tokens(conn: psycopg.Connection, user: AuthUser) -> tuple[str, str]:
    """Return (access JWT, refresh token) and persist the refresh token's hash."""
    refresh, refresh_hash = new_refresh_token()
    expires = datetime.now(UTC) + timedelta(days=get_settings().refresh_token_days)
    conn.execute(
        "INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at) VALUES (%s, %s, %s, %s)",
        (uuid.uuid4(), user.id, refresh_hash, expires),
    )
    return create_access_token(user.id, user.token_version), refresh


def rotate_refresh(conn: psycopg.Connection, refresh: str) -> tuple[AuthUser, str, str] | None:
    """Swap a valid refresh token for a new pair. Reusing a rotated token revokes every
    session of that user (RefreshReuseError). Returns None for unknown/expired tokens."""
    row = conn.execute(
        """SELECT id, user_id, expires_at, revoked_at FROM refresh_tokens
           WHERE token_hash = %s FOR UPDATE""",
        (hash_refresh_token(refresh),),
    ).fetchone()
    if row is None:
        return None
    token_id, user_id, expires_at, revoked_at = row
    if revoked_at is not None:
        revoke_all_sessions(conn, user_id)
        raise RefreshReuseError
    user = load_user(conn, user_id)
    if expires_at <= datetime.now(UTC) or user is None or not user.is_active:
        return None
    conn.execute("UPDATE refresh_tokens SET revoked_at = now() WHERE id = %s", (token_id,))
    access, new_refresh = issue_tokens(conn, user)
    return user, access, new_refresh


def revoke_refresh(conn: psycopg.Connection, refresh: str) -> None:
    conn.execute(
        "UPDATE refresh_tokens SET revoked_at = now() WHERE token_hash = %s AND revoked_at IS NULL",
        (hash_refresh_token(refresh),),
    )


def revoke_all_sessions(conn: psycopg.Connection, user_id: UUID) -> None:
    """Revoke every refresh token and invalidate outstanding access tokens (token_version)."""
    conn.execute(
        "UPDATE refresh_tokens SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL",
        (user_id,),
    )
    conn.execute(
        "UPDATE users SET token_version = token_version + 1, updated_at = now() WHERE id = %s",
        (user_id,),
    )
