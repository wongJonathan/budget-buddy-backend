"""index transactions by expense

Revision ID: e4b2d9c61a07
Revises: c3e8a1d5f7b2
Create Date: 2026-09-30 12:00:00.000000

Postgres does not index a foreign key's referencing column, so every lookup by
`expense_id` - Allocated on each Expense read, and the withdraw/Restore set when an
Expense is deleted or brought back - was a scan of every user's Transactions. A plain
index rather than one partial on `deleted_at IS NULL`: withdrawal and Restore read the
deleted rows too.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4b2d9c61a07"
down_revision: str | Sequence[str] | None = "c3e8a1d5f7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index("ix_transactions_expense_id", "transactions", ["expense_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_transactions_expense_id", table_name="transactions")
