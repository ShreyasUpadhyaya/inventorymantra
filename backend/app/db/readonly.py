"""Read-only, role-scoped database sessions (the only way agent SQL reaches the DB).

Each app role maps to its own Postgres login role, which can read only its view schema
(migration 0003). A session is a READ ONLY transaction carrying the per-request context
(app.user_id, app.today) that the my_* views and date maths rely on.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Literal
from urllib.parse import quote, urlsplit
from uuid import UUID

import psycopg

from app.core.config import get_settings
from app.db.session import psycopg_dsn

AppRole = Literal["inventory_manager", "procurement_manager", "owner"]

DB_ROLE: dict[AppRole, str] = {
    "inventory_manager": "rms_im_ro",
    "procurement_manager": "rms_pm_ro",
    "owner": "rms_owner_ro",
}
VIEW_SCHEMA: dict[AppRole, str] = {
    "inventory_manager": "v_im",
    "procurement_manager": "v_pm",
    "owner": "v_owner",
}


def role_dsn(role: AppRole) -> str:
    password = get_settings().readonly_db_password
    if password is None:
        raise RuntimeError("READONLY_DB_PASSWORD is not set (see .env.example).")
    base = urlsplit(psycopg_dsn())
    netloc = f"{DB_ROLE[role]}:{quote(password.get_secret_value(), safe='')}@{base.hostname}"
    if base.port:
        netloc += f":{base.port}"
    return base._replace(netloc=netloc).geturl()


@contextmanager
def readonly_session(
    role: AppRole, user_id: UUID | None, today: date
) -> Iterator[psycopg.Connection]:
    """Yield a connection inside a READ ONLY transaction; always rolled back on exit."""
    conn = psycopg.connect(role_dsn(role), connect_timeout=5)
    try:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SELECT set_config('app.today', %s, true)", (today.isoformat(),))
        conn.execute(
            "SELECT set_config('app.user_id', %s, true)", (str(user_id) if user_id else "",)
        )
        yield conn
    finally:
        conn.rollback()
        conn.close()
