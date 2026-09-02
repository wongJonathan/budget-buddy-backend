"""transaction as ledger; drop category system_type; period as first-of-month

Revision ID: a1f4c07be3d2
Revises: c9811376a096
Create Date: 2026-09-02 11:04:22.118430

Four decisions land here at once, because they share data steps and re-run the same
enum surgery. See docs/adr/0009 (income is a Transaction with no Expense) and
docs/adr/0010 (Rollover records what it rolled).

1. `transactions.expense_id` becomes nullable, and `transactions.name` is added as
   NOT NULL. Autogenerate would emit a single `add_column(nullable=False)`, which only
   works on an empty table - split into add-nullable -> backfill -> SET NOT NULL, the
   same shape as c9811376a096. The backfill takes the parent Expense's name, falling
   back to `note` and then to a literal, so every existing row gets something
   meaningful rather than a placeholder; `expense_id` is still NOT NULL at that point,
   so the join always matches and no row is left null.

2. Any Expense that was standing in for income - one under a Category that carried
   `system_type = 'income'` - is collapsed. Its Transactions lose their `expense_id`
   and take the Expense's name, and the Expense itself is deleted. This runs *before*
   the column drop in step 3, because `system_type` is how those rows are found; after
   the drop there is no way to identify them, and the migration is not reversible in
   that respect.

3. `categories.system_type` and the `category_system_type` enum type are dropped.
   A Category is now a plain user label.

4. `expenses.period` is truncated to the first of its month and constrained to stay
   that way. Existing rows were written with `date.today()`, so they carry real
   day-of-month values that would fail the CHECK. `rolled_periods` arrives in the same
   revision because it is the other half of the Rollover groundwork.

   If that UPDATE trips `uq_expense_budget_period_series`, it means two rows in one
   Budget share a `series_id` on different days of the same month. `series_id` defaults
   to `gen_random_uuid()` per insert, so that should not exist - failing loudly is the
   right outcome, and the rows want looking at by hand rather than a coercion here.

`transaction_type` keeps its `income` member: income is still a Transaction, it just
no longer points at an Expense. Only `category_system_type` goes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1f4c07be3d2"
down_revision: str | Sequence[str] | None = "c9811376a096"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. transactions.name -----------------------------------------------------
    op.add_column("transactions", sa.Column("name", sa.String(), nullable=True))
    op.execute(
        """
        UPDATE transactions AS t
        SET name = COALESCE(NULLIF(e.name, ''), NULLIF(t.note, ''), 'Transaction')
        FROM expenses AS e
        WHERE e.id = t.expense_id
        """
    )
    op.alter_column("transactions", "name", nullable=False)

    # 2. collapse income Expenses into bare income Transactions ----------------
    # Order matters twice over. The column has to accept nulls before anything writes
    # one, and the Transactions have to be re-pointed before their Expenses go, since
    # transactions.expense_id is ON DELETE CASCADE - deleting first would take the
    # Transactions with it.
    op.alter_column("transactions", "expense_id", nullable=True)
    op.execute(
        """
        UPDATE transactions AS t
        SET expense_id = NULL,
            name = COALESCE(NULLIF(e.name, ''), t.name)
        FROM expenses AS e
        JOIN categories AS c ON c.id = e.category_id
        WHERE e.id = t.expense_id
          AND c.system_type = 'income'
        """
    )
    op.execute(
        """
        DELETE FROM expenses AS e
        USING categories AS c
        WHERE c.id = e.category_id
          AND c.system_type = 'income'
        """
    )

    # 3. drop system_type ------------------------------------------------------
    op.drop_column("categories", "system_type")
    op.execute("DROP TYPE category_system_type")

    # 4. periods, and the Rollover record --------------------------------------
    op.execute("UPDATE expenses SET period = date_trunc('month', period)::date")
    op.create_check_constraint(
        "ck_expense_period_first_of_month", "expenses", "EXTRACT(DAY FROM period) = 1"
    )

    op.create_table(
        "rolled_periods",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("budget_id", sa.UUID(), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column(
            "rolled_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["budget_id"], ["budgets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("budget_id", "period", name="uq_rolled_period_budget_period"),
    )
    op.create_index("ix_rolled_periods_budget_id", "rolled_periods", ["budget_id"])


def downgrade() -> None:
    """Downgrade schema.

    Structurally reversible; not reversible in data. The income Expenses deleted in
    step 2 are gone, and their Transactions stay `expense_id IS NULL` - which the
    restored NOT NULL cannot accept, so they are deleted too rather than silently
    reattached to an arbitrary Expense.
    """
    op.drop_index("ix_rolled_periods_budget_id", table_name="rolled_periods")
    op.drop_table("rolled_periods")

    op.drop_constraint("ck_expense_period_first_of_month", "expenses", type_="check")

    op.execute("CREATE TYPE category_system_type AS ENUM ('income', 'saving_goal', 'debt')")
    op.add_column(
        "categories",
        sa.Column(
            "system_type",
            # create_type=False: the CREATE TYPE above already made it, and add_column
            # would otherwise emit a second one and fail on "type already exists".
            postgresql.ENUM(name="category_system_type", create_type=False),
            nullable=True,
        ),
    )

    op.execute("DELETE FROM transactions WHERE expense_id IS NULL")
    op.alter_column("transactions", "expense_id", nullable=False)
    op.drop_column("transactions", "name")
