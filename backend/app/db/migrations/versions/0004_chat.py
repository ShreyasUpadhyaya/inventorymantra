"""Chat threads and messages (C4.5). The server owns conversation history.

LangGraph's own checkpoint tables are created by PostgresSaver.setup() at start-up; both
live in `public`, which the read-only role users cannot reach.

Revision ID: 0004_chat
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_chat"
down_revision: str | None = "0003_role_views"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

UPGRADE_SQL = """
CREATE TABLE chat_threads (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id),
    title text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_chat_threads_user ON chat_threads (user_id, updated_at DESC);

CREATE TABLE chat_messages (
    id uuid PRIMARY KEY,
    thread_id uuid NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    role varchar NOT NULL CHECK (role IN ('user', 'assistant')),
    content text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,  -- assistant: route, viz, rows, options...
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_chat_messages_thread ON chat_messages (thread_id, created_at);
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_messages, chat_threads")
