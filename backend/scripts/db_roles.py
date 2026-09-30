"""Enable login for the read-only role users (created NOLOGIN by migration 0003).

The password comes from READONLY_DB_PASSWORD and is never printed. Without it the roles
stay NOLOGIN, so nothing can connect as them.
"""

import psycopg
from psycopg import sql

from app.core.config import get_settings
from app.db.readonly import DB_ROLE


def configure_role_logins(conn: psycopg.Connection) -> bool:
    password = get_settings().readonly_db_password
    for role in DB_ROLE.values():
        if password is None:
            conn.execute(sql.SQL("ALTER ROLE {} NOLOGIN").format(sql.Identifier(role)))
        else:
            conn.execute(
                sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password.get_secret_value())
                )
            )
    return password is not None
