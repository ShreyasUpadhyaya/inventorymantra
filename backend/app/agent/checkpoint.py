"""Where paused turns are saved (DECISION D-034).

Postgres by default, so a clarification survives restarts and works across API workers.
PostgresSaver needs autocommit connections with dict rows, so it gets its own small pool.
Only our own state models may be rebuilt from a checkpoint.
"""

from functools import lru_cache

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.core.config import get_settings
from app.db.session import psycopg_dsn

CHECKPOINT_TYPES = [("app.agent.state", "GuardOutput"), ("app.agent.state", "EntityMention")]


def serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)


@lru_cache
def get_checkpointer() -> BaseCheckpointSaver:  # type: ignore[type-arg]
    if get_settings().agent_checkpointer == "memory":
        return InMemorySaver(serde=serializer())
    pool = ConnectionPool(
        psycopg_dsn(),
        min_size=1,
        max_size=5,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row,
                "connect_timeout": 5},
        open=True,
    )  # fmt: skip
    saver = PostgresSaver(pool, serde=serializer())  # type: ignore[arg-type]
    saver.setup()  # idempotent: creates/migrates LangGraph's checkpoint tables
    return saver
