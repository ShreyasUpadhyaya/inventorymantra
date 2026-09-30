from collections.abc import Iterator

import psycopg
import pytest

from app.db.session import psycopg_dsn


@pytest.fixture(scope="session")
def db() -> Iterator[psycopg.Connection]:
    """Connection to the seeded dev/CI database; tests using it skip if it's unreachable."""
    try:
        conn = psycopg.connect(psycopg_dsn(), connect_timeout=3, autocommit=True)
    except (RuntimeError, psycopg.OperationalError) as exc:
        pytest.skip(f"database not available: {exc.__class__.__name__}")
    with conn:
        yield conn
