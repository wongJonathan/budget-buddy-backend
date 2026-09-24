"""creatable model timestamps replace is_deleted

Revision ID: c504ad40af7f
Revises: c8768c6b4e05
Create Date: 2026-09-24 14:47:23.497974

Budget, Expense, Savings, Transaction and Category now share `CreatableModel`:
`created_at`, `updated_at` and a nullable `deleted_at`, which replaces the `is_deleted`
boolean. A row is deleted exactly when `deleted_at IS NOT NULL`. Category never had an
`is_deleted` (it hard-deletes, ADR 0007), so it gains the columns with nothing to
backfill.

The timestamp is not just a richer flag. ADR 0014's Restore finds the Transactions a
deleted Expense withdrew by `deleted_at` equal to the Expense's, which a boolean cannot
express.

Autogenerate emitted this as add-then-drop, which would silently undelete every
soft-deleted row. Each table instead gains `deleted_at`, backfills it from `is_deleted`,
and only then drops the flag. The downgrade reverses that, so the round trip keeps which
rows are deleted but not when they were.

Backfilled rows take the migration's own `now()`, the same value in every table, because
nothing ever recorded when they were deleted. As a result, every Transaction deleted
before this migration shares its `deleted_at` with every Expense deleted before it, and
ADR 0014's equality match cannot tell a withdrawn Transaction from one the User deleted
separately. Restore only reaches the open Period, so this can only matter for legacy
deletes made in the Period this migration runs in.

Existing Expense, Savings and Category rows likewise get `now()` for `created_at`. Budget and
Transaction already had one and keep it. `updated_at` starts at `now()` everywhere.

Nothing here keeps `updated_at` current - that is `onupdate=func.now()` on
`CreatableModel`, which SQLAlchemy applies to the UPDATEs it emits and which leaves no
trace in the schema. Raw SQL, including a later migration's `op.execute`, does not bump
it. Likewise `deleted_at` is only ever written by the services, as `func.now()` or `None`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c504ad40af7f"
down_revision: str | Sequence[str] | None = "c8768c6b4e05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("budgets", "expenses", "savings", "transactions", "categories")
# Budget and Transaction already carry `created_at`, so it is neither added nor dropped.
_NEEDS_CREATED_AT = ("expenses", "savings", "categories")
# Category hard-deleted until now, so there is no flag to carry over or restore.
_HAS_IS_DELETED = ("budgets", "expenses", "savings", "transactions")


def _timestamp(name: str) -> sa.Column[sa.DateTime]:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    """Upgrade schema."""
    for table in _TABLES:
        if table in _NEEDS_CREATED_AT:
            op.add_column(table, _timestamp("created_at"))
        op.add_column(table, _timestamp("updated_at"))
        op.add_column(table, sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        if table in _HAS_IS_DELETED:
            op.execute(f"UPDATE {table} SET deleted_at = now() WHERE is_deleted")
            op.drop_column(table, "is_deleted")


def downgrade() -> None:
    """Downgrade schema."""
    for table in reversed(_TABLES):
        if table in _HAS_IS_DELETED:
            op.add_column(
                table,
                sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
            )
            op.execute(f"UPDATE {table} SET is_deleted = true WHERE deleted_at IS NOT NULL")
        op.drop_column(table, "deleted_at")
        op.drop_column(table, "updated_at")
        if table in _NEEDS_CREATED_AT:
            op.drop_column(table, "created_at")
