"""Questions and answers on a PO waiting for approval (C6.6): the Owner asks, the requester
replies, and the PO keeps waiting until the Owner decides.

Revision ID: 0006_approval_messages
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_approval_messages"
down_revision: str | None = "0005_password_change"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE approval_messages (
               id uuid PRIMARY KEY,
               approval_id uuid NOT NULL REFERENCES approvals(id) ON DELETE CASCADE,
               author_id uuid NOT NULL REFERENCES users(id),
               kind varchar NOT NULL CHECK (kind IN ('question', 'reply')),
               body text NOT NULL CHECK (length(body) BETWEEN 1 AND 500),
               created_at timestamptz NOT NULL DEFAULT now()
           );
           CREATE INDEX ix_approval_messages ON approval_messages (approval_id, created_at)"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE approval_messages")
