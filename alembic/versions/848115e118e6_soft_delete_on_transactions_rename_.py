"""soft delete on transactions; rename expense flag to is_deleted

Revision ID: 848115e118e6
Revises: 9dd491ad9ae9
Create Date: 2026-08-31 17:32:11.750169

Autogenerate rendered the expenses rename as add_column + drop_column, which would
discard the flag for every existing row. Replaced with alter_column(new_column_name=...)
so the data comes along.

No foreign key changes here on purpose: the database's ON DELETE actions were already
correct, and `expenses.category_id` was reverted in the model (SET NULL -> RESTRICT) to
match what the database has always had. See docs/adr/0007.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "848115e118e6"
down_revision: str | Sequence[str] | None = "9dd491ad9ae9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column("expenses", "is_deactivated", new_column_name="is_deleted")
    op.add_column(
        "transactions",
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("transactions", "is_deleted")
    op.alter_column("expenses", "is_deleted", new_column_name="is_deactivated")
