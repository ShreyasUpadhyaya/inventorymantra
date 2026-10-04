"""Drop, recreate and seed the test database (D-051), so tests and evals always start from
the frozen seed data, whatever was written in the demo database.

Run from backend/:  uv run python -m scripts.test_db   (make test-db)
"""

from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

from app.db.session import psycopg_dsn, use_test_database
from scripts import seed


def main() -> None:
    name = use_test_database()
    if name is None:
        raise SystemExit("DATABASE_URL is not set (see .env.example).")
    # the server's maintenance database: never connected to the one being dropped
    admin_dsn = urlunsplit(urlsplit(psycopg_dsn())._replace(path="/postgres"))
    with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=10) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    print(f"Test database {name}: recreated; seeding.")
    seed.main()


if __name__ == "__main__":
    main()
