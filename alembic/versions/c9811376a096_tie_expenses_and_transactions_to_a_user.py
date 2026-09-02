"""tie expenses and transactions to a user

Revision ID: c9811376a096
Revises: 848115e118e6
Create Date: 2026-09-01 17:22:57.582788

Both columns are NOT NULL in the models, but autogenerate's single
`add_column(nullable=False)` only works on an empty table - it fails outright
against any environment that already has rows. Split into add-nullable ->
backfill -> SET NOT NULL so existing data survives.

The backfill derives ownership from the row's existing parent chain rather than
guessing: an expense belongs to whoever owns its budget, and a transaction to
whoever owns its expense (so expenses has to be filled first). Both parent FKs
are already NOT NULL, so every row gets a value and the SET NOT NULL holds.

Constraints are named explicitly - autogenerate emitted `None`, which Postgres
would auto-name on the way up but leaves `downgrade()` with no name to drop.
The names match the `<table>_<column>_fkey` default the rest of the schema uses.

Postgres does not index a foreign key automatically, and every list query is
about to filter by owner, so both columns get one - `ix_<table>_<column>`, the
same shape `sessions.user_id` already uses.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9811376a096"
down_revision: str | Sequence[str] | None = "848115e118e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("expenses", sa.Column("user_id", sa.UUID(), nullable=True))
    op.add_column("transactions", sa.Column("user_id", sa.UUID(), nullable=True))

    op.execute(
        """
        UPDATE expenses AS e
        SET user_id = b.user_id
        FROM budgets AS b
        WHERE b.id = e.budget_id
        """
    )
    op.execute(
        """
        UPDATE transactions AS t
        SET user_id = e.user_id
        FROM expenses AS e
        WHERE e.id = t.expense_id
        """
    )

    op.alter_column("expenses", "user_id", nullable=False)
    op.alter_column("transactions", "user_id", nullable=False)

    op.create_foreign_key(
        "expenses_user_id_fkey",
        "expenses",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "transactions_user_id_fkey",
        "transactions",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.create_index("ix_expenses_user_id", "expenses", ["user_id"])
    op.create_index("ix_transactions_user_id", "transactions", ["user_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_transactions_user_id", table_name="transactions")
    op.drop_index("ix_expenses_user_id", table_name="expenses")
    op.drop_constraint("transactions_user_id_fkey", "transactions", type_="foreignkey")
    op.drop_column("transactions", "user_id")
    op.drop_constraint("expenses_user_id_fkey", "expenses", type_="foreignkey")
    op.drop_column("expenses", "user_id")
