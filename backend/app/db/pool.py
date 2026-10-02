"""Connection pool for the app's own DB user (auth, writes, dashboards built on views).

Agent SQL never uses this pool; it runs through app.db.readonly as a role user.
"""

from psycopg_pool import ConnectionPool

from app.db.session import psycopg_dsn

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            psycopg_dsn(), min_size=1, max_size=10, kwargs={"connect_timeout": 5}, open=True
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
