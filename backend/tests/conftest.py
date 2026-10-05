from collections.abc import Iterator

import psycopg
import pytest

from app.db.session import psycopg_dsn, use_test_database

# Tests run against the test database (DATABASE_URL's name + "_test", made by `make test-db`),
# never the demo one, so writes made while trying the app can't break them (D-051).
TEST_DATABASE = use_test_database()


@pytest.fixture(scope="session")
def db() -> Iterator[psycopg.Connection]:
    """Connection to the seeded test database; tests using it skip if Postgres is unreachable."""
    try:
        conn = psycopg.connect(psycopg_dsn(), connect_timeout=3, autocommit=True)
    except (RuntimeError, psycopg.OperationalError) as exc:
        if "does not exist" in str(exc):  # the server is up: a skip would hide every DB test
            pytest.fail(f"test database {TEST_DATABASE} is missing: run `make test-db`")
        pytest.skip(f"database not available: {exc.__class__.__name__}")
    with conn:
        yield conn
