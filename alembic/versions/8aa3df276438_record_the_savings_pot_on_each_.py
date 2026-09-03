"""record the savings fund on each transaction

Revision ID: 8aa3df276438
Revises: 88f879caa7c7
Create Date: 2026-09-02 22:41:03.884512

A fund balance was derived by joining `transactions.expense_id -> expenses.savings_id`.
That reads the fund a lineage *currently feeds*, when what a balance needs is the fund the
money *went into* - the same answer today, and different answers the moment Activation
gains a reallocation map and starts re-pointing a lineage. Deriving it would silently
move every historical SAVE to the new fund and empty the old one.

It also made a balance depend on Expense and Budget visibility: soft-deleting either put
the join's rows out of `live_transactions` and the fund read zero while the money was
still real.

So the fund is recorded on the Transaction. The two columns are not duplicates:

- `transactions.savings_id` - which fund this money moved. Historical, never rewritten.
- `expenses.savings_id`     - which fund this lineage feeds now. Re-pointable later.

Non-null on exactly the rows that move a fund: SAVE, SPEND_SAVED, and the fund side of a
TRANSFER pair. The backfill below sets it for existing rows from the lineage they belong
to, which is correct precisely because nothing has been re-pointed yet.

See docs/adr/0011.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8aa3df276438"
down_revision: str | Sequence[str] | None = "88f879caa7c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK_TRANSACTIONS_SAVINGS = "fk_transactions_savings_id"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("transactions", sa.Column("savings_id", sa.UUID(), nullable=True))
    op.create_index(
        op.f("ix_transactions_savings_id"), "transactions", ["savings_id"], unique=False
    )
    op.create_foreign_key(
        _FK_TRANSACTIONS_SAVINGS,
        "transactions",
        "savings",
        ["savings_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Backfill from the lineage. Restricted to the fund-moving types so a plain SPEND
    # against an Expense that happens to have a fund is not mislabelled as having drawn
    # from it - only SAVE, SPEND_SAVED and a transfer's fund side ever move a balance.
    op.execute(
        sa.text(
            """
            UPDATE transactions t
               SET savings_id = e.savings_id
              FROM expenses e
             WHERE e.id = t.expense_id
               AND e.savings_id IS NOT NULL
               AND t.type IN ('save', 'spend_saved', 'transfer')
            """
        )
    )


def downgrade() -> None:
    """Downgrade schema.

    Safe to drop: until a lineage is re-pointed the column is reproducible from
    `expense_id -> expenses.savings_id`, which is what the balance query did before.
    """
    op.drop_constraint(_FK_TRANSACTIONS_SAVINGS, "transactions", type_="foreignkey")
    op.drop_index(op.f("ix_transactions_savings_id"), table_name="transactions")
    op.drop_column("transactions", "savings_id")
