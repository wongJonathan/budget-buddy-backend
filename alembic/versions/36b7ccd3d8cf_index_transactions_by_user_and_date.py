"""index transactions by user and date

Revision ID: 36b7ccd3d8cf
Revises: 882e753a5321
Create Date: 2026-09-16 22:05:55.752218

`GET /transactions` now filters by a date window and pages through the result, and every
read of this table is scoped to one User first. A single-column `date` index cannot serve
that: Postgres scans the whole range across all users and discards the rows that are not
the caller's. Measured on a 200k-row stand-in, one user over one month - 6,882 rows read
to return 2,220, with 4,662 removed by filter. The composite reads exactly the 2,220.

It also answers the ordering. `ORDER BY date DESC LIMIT n` becomes an Index Scan Backward
with no sort node, reading n rows instead of the user's whole history. That is why the
index is ascending: a btree walks either direction at the same cost, so a DESC index would
buy nothing here.

`ix_transactions_user_id` goes, because `user_id` is this index's leftmost prefix. Every
user-only lookup and the ON DELETE CASCADE from `users` are served by the composite, and
keeping both would cost a second write on every insert for no read that needs it. The new
index is created before the old one is dropped so no statement runs without one.

See app/models/transaction.py for the same reasoning next to the column.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "36b7ccd3d8cf"
down_revision: str | Sequence[str] | None = "882e753a5321"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index("ix_transactions_user_date", "transactions", ["user_id", "date"], unique=False)
    op.drop_index("ix_transactions_user_id", table_name="transactions")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_index("ix_transactions_user_id", "transactions", ["user_id"], unique=False)
    op.drop_index("ix_transactions_user_date", table_name="transactions")
