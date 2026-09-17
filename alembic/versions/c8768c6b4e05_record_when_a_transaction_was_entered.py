"""record when a transaction was entered

Revision ID: c8768c6b4e05
Revises: 36b7ccd3d8cf
Create Date: 2026-09-17 09:41:12.006331

`transactions.date` is when the money moved, as the User asserts it - day-granular, and
editable, because a row entered today can record last week's purchase. It therefore says
nothing about the order rows were entered in, and several Transactions a day is ordinary.

The listing needed a total order, and until now got it from `ORDER BY date, id`. `id` is
`gen_random_uuid()`, a v4 UUID with no time component, so that ordering is stable but
arbitrary: five rows inserted 1..5 come back 5, 2, 4, 3, 1. Stability is all pagination
needs - without a total order a row can surface on two pages or on neither - but it is
not an answer to "which did I enter first".

`created_at` is that answer, and is trustworthy for it precisely because no client can
write it: it is absent from `TransactionCreate` and `TransactionUpdate` while `date`
stays editable. The listing now orders `date DESC, created_at DESC, id DESC`.

`id` stays last rather than being dropped. `now()` is transaction-start time, so every
row a single request writes shares a `created_at` - the spend split's SPEND_SAVED and
SPEND pair ties here exactly, the same way it already tied on `date`. Their relative
order is arbitrary in the domain; `id` makes it arbitrary *and repeatable*.

The index gains `created_at` for the same reason it gained `date`: so it can supply the
ordering, not just find the rows. With `user_id` pinned by equality the matching entries
are one contiguous run already sorted by the rest of the key, and Postgres walks it
backwards and stops at LIMIT. It stops at three columns - a fourth would only remove a
sort over rows sharing a date *and* a created_at, which is rows written in one database
transaction, at most two of them.

Existing rows take the migration's own `now()`. That is a lie about when they were
entered, and an unrecoverable one - nothing in the schema ever recorded it. The only data
is test data, and every such row also shares a value, so they fall back to `id` and stay
consistently ordered.

See docs/adr/0013.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8768c6b4e05"
down_revision: str | Sequence[str] | None = "36b7ccd3d8cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "transactions",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # Created before the old one is dropped, so no statement runs unindexed.
    op.create_index(
        "ix_transactions_user_date_created",
        "transactions",
        ["user_id", "date", "created_at"],
        unique=False,
    )
    op.drop_index("ix_transactions_user_date", table_name="transactions")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_index("ix_transactions_user_date", "transactions", ["user_id", "date"], unique=False)
    op.drop_index("ix_transactions_user_date_created", table_name="transactions")
    op.drop_column("transactions", "created_at")
