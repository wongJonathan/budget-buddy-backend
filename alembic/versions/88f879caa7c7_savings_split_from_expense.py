"""savings split from expense

Revision ID: 88f879caa7c7
Revises: a1f4c07be3d2
Create Date: 2026-09-02 22:16:52.360734

`Expense.amount_saved` becomes the `savings` table, and Expense points at it through a
nullable `savings_id`. See docs/adr/0011 for why a saved balance could not stay on an
Expense: an Expense is instantiated once per Period, while a balance accumulates across
them, so the column was duplicated into every month and every competing draft Budget.

Three things worth knowing about the shape here:

- `expenses.savings_id` gets an index but deliberately **no UNIQUE**. Every Period's row
  of one lineage points at the same fund, so a unique constraint would reject next
  month's row the first time Rollover ran.

- `ON DELETE SET NULL` rather than CASCADE. A fund is soft-deleted in normal use, so this
  fires only when a User is hard-deleted; an Expense row outliving its fund reference is
  the right outcome there, and losing the Expense would take its Transactions with it.

- `amount_saved` is dropped and its values discarded, which is only safe because there
  are no real users yet and the sole writer of a non-zero value was the JSON import.
  The guard below enforces that assumption rather than trusting it: a derived balance
  can only account for money that has a ledger row, so dropping a populated column
  silently makes every fund read low. If it fires, the fix is not to delete the guard -
  it is to turn each non-zero `amount_saved` into a `SAVE` Transaction dated to its
  row's `period`, which is exactly what `_import_amount_saved` does for the JSON path.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "88f879caa7c7"
down_revision: str | Sequence[str] | None = "a1f4c07be3d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Autogenerate emits `None` for this, which works on create and then fails on
# downgrade's drop_constraint - a name it can look up is the whole point.
_FK_EXPENSES_SAVINGS = "fk_expenses_savings_id"


def _refuse_to_discard_real_savings() -> None:
    """Stop rather than silently delete money."""
    populated = op.get_bind().scalar(
        sa.text("SELECT count(*) FROM expenses WHERE amount_saved <> 0")
    )
    if populated:
        raise RuntimeError(
            f"{populated} expense row(s) still carry a non-zero amount_saved. This "
            "migration discards that column, which would lose the money for good - a "
            "derived balance can only count what has a ledger row. Convert each one "
            "into a SAVE transaction dated to its row's period first (see "
            "docs/adr/0011), or zero them if they are disposable test data."
        )


def upgrade() -> None:
    """Upgrade schema."""
    _refuse_to_discard_real_savings()

    op.create_table(
        "savings",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_savings_user_id"), "savings", ["user_id"], unique=False)

    op.add_column("expenses", sa.Column("savings_id", sa.UUID(), nullable=True))
    op.create_index(op.f("ix_expenses_savings_id"), "expenses", ["savings_id"], unique=False)
    op.create_foreign_key(
        _FK_EXPENSES_SAVINGS,
        "expenses",
        "savings",
        ["savings_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_column("expenses", "amount_saved")


def downgrade() -> None:
    """Downgrade schema.

    The column comes back empty. Balances live in the ledger from here on, and there is
    no rule for collapsing a fund's Transactions back into one figure per Expense row -
    the fund spans Periods and the column does not.
    """
    op.add_column(
        "expenses",
        sa.Column(
            "amount_saved",
            sa.NUMERIC(precision=12, scale=2),
            server_default=sa.text("'0'::numeric"),
            autoincrement=False,
            nullable=False,
        ),
    )
    op.drop_constraint(_FK_EXPENSES_SAVINGS, "expenses", type_="foreignkey")
    op.drop_index(op.f("ix_expenses_savings_id"), table_name="expenses")
    op.drop_column("expenses", "savings_id")
    op.drop_index(op.f("ix_savings_user_id"), table_name="savings")
    op.drop_table("savings")
