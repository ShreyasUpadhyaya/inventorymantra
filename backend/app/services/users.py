"""User management, shared by the Users page API (C3.2/C5.1b) and the chat actions (C6.4),
so both follow one code path. Temporary passwords are generated here, returned to the
caller once, and never stored, logged or sent to a model."""

import re
import secrets
import uuid
from uuid import UUID

import psycopg

from app.auth.passwords import hash_password
from app.auth.service import AuthUser, Role, normalise_email, revoke_all_sessions, set_password

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ROLES: tuple[Role, ...] = ("inventory_manager", "procurement_manager", "owner")


class UserError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def temp_password() -> str:
    return secrets.token_urlsafe(12)


def check_new(conn: psycopg.Connection, email: str, full_name: str, role: str) -> str:
    """Validate a new account; returns the normalised email."""
    email = normalise_email(email)
    if not EMAIL_RE.match(email):
        raise UserError(422, "That isn't a valid email address.")
    if not full_name.strip():
        raise UserError(422, "Please give the person's name.")
    if role not in ROLES:
        raise UserError(422, "Choose a role: Inventory Manager, Procurement Manager or Owner.")
    if conn.execute("SELECT 1 FROM users WHERE email = %s", (email,)).fetchone():
        raise UserError(409, "A user with this email already exists.")
    return email


def create(
    conn: psycopg.Connection, owner: AuthUser, email: str, full_name: str, role: str
) -> tuple[UUID, str]:
    """New account with a temporary password it must change at first sign-in."""
    email = check_new(conn, email, full_name, role)
    password, user_id = temp_password(), uuid.uuid4()
    try:
        conn.execute(
            """INSERT INTO users (id, email, password_hash, full_name, role, created_by,
                                  must_change_password)
               VALUES (%s, %s, %s, %s, %s, %s, true)""",
            (user_id, email, hash_password(password), full_name.strip(), role, owner.id),
        )
    except psycopg.errors.UniqueViolation:
        raise UserError(409, "A user with this email already exists.") from None
    return user_id, password


def set_active(conn: psycopg.Connection, owner: AuthUser, user_id: UUID, active: bool) -> bool:
    """Returns the previous state. Deactivating signs the person out everywhere at once."""
    row = conn.execute("SELECT is_active FROM users WHERE id = %s", (user_id,)).fetchone()
    if row is None:
        raise UserError(404, "User not found.")
    if user_id == owner.id and not active:
        raise UserError(400, "You can't deactivate your own account.")
    conn.execute(
        "UPDATE users SET is_active = %s, updated_at = now() WHERE id = %s", (active, user_id)
    )
    if not active:
        revoke_all_sessions(conn, user_id)
    return bool(row[0])


def reset_password(conn: psycopg.Connection, owner: AuthUser, user_id: UUID) -> str:
    if conn.execute("SELECT 1 FROM users WHERE id = %s", (user_id,)).fetchone() is None:
        raise UserError(404, "User not found.")
    if user_id == owner.id:
        raise UserError(400, "Change your own password from your account page.")
    password = temp_password()
    set_password(conn, user_id, password, must_change=True)
    return password
