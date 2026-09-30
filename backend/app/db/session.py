"""Database connection helpers."""

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
