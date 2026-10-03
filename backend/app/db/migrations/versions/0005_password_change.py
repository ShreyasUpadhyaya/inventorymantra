"""Users created or reset by the Owner must change their temporary password (C5.1b).

Revision ID: 0005_password_change
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_password_change"
down_revision: str | None = "0004_chat"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN must_change_password boolean NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN must_change_password")
