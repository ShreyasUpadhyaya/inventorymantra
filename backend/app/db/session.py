"""Database connection helpers."""

import os
from urllib.parse import urlsplit, urlunsplit

from app.core.config import get_settings


def sqlalchemy_url() -> str:
    url = get_settings().database_url
    if url is None:
        raise RuntimeError("DATABASE_URL is not set (see .env.example).")
    return url.get_secret_value()


def psycopg_dsn() -> str:
    """psycopg wants a plain libpq URL, without SQLAlchemy's '+driver' suffix."""
    scheme, rest = sqlalchemy_url().split("://", 1)
    return f"{scheme.split('+', 1)[0]}://{rest}"


def database_url_for_tests() -> str:
    """TEST_DATABASE_URL, else DATABASE_URL with "_test" added to the database name (D-051).
    The name must end in "_test", so tests and evals can never run against the demo data."""
    explicit = get_settings().test_database_url
    parts = urlsplit(explicit.get_secret_value() if explicit else sqlalchemy_url())
    name = parts.path.lstrip("/")
    if not explicit and not name.endswith("_test"):
        name += "_test"
    if not name.endswith("_test"):
        raise RuntimeError("TEST_DATABASE_URL must name a database ending in '_test'.")
    return urlunsplit(parts._replace(path=f"/{name}"))


def use_test_database() -> str | None:
    """Point this process at the test database (pytest, evals). Returns its name, or None
    when no database is configured at all (the database tests then skip)."""
    if get_settings().database_url is None and get_settings().test_database_url is None:
        return None
    url = database_url_for_tests()
    os.environ["DATABASE_URL"] = url  # the environment wins over .env for every later read
    get_settings.cache_clear()
    return urlsplit(url).path.lstrip("/")
