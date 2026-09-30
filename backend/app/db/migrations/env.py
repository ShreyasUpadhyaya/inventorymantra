"""Alembic environment. The DB URL comes from Settings (.env), never from alembic.ini."""

from alembic import context
from sqlalchemy import create_engine, pool

from app.db.session import sqlalchemy_url


def run_migrations_online() -> None:
    engine = create_engine(sqlalchemy_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported; run against a database.")
run_migrations_online()
