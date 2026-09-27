"""derive transactions.savings_id from the expense instead of storing it

Revision ID: 5d2e9b7a41c3
Revises: c504ad40af7f
Create Date: 2026-09-27 12:00:00.000000

Reverses 8aa3df276438. `Transaction.savings_id` is now a `column_property` reading
`expenses.savings_id` through `expense_id`, gated on the fund-moving types, so the
stored column has nothing left to do. Nothing re-points `expenses.savings_id` today, so
the derived value matches what was stored for every existing row - dropping it loses
no information.

See docs/adr/0015.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5d2e9b7a41c3"
down_revision: str | Sequence[str] | None = "c504ad40af7f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK_TRANSACTIONS_SAVINGS = "fk_transactions_savings_id"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(_FK_TRANSACTIONS_SAVINGS, "transactions", type_="foreignkey")
    op.drop_index(op.f("ix_transactions_savings_id"), table_name="transactions")
    op.drop_column("transactions", "savings_id")


def downgrade() -> None:
    """Downgrade schema, backfilling with the same rule the derivation uses."""
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
