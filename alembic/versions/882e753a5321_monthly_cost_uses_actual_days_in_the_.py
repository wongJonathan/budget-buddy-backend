"""monthly_cost uses actual days in the period

Revision ID: 882e753a5321
Revises: 8aa3df276438
Create Date: 2026-09-16 16:02:39.837849

`daily` converted with `cost * 30` and `weekly` with `cost * 52.0 / 12`. Those constants
disagree about how long a month is - 30.00 days against 30.33 - so one real expense got
two different allocations depending only on how the user chose to express it: $10/day
read $300.00 a month, the identical $70/week read $303.33. `30` is also the banker's
month from the 30/360 day count, which accrues interest on bonds; twelve of them is a
360-day year, so a daily expense was under-allocated by five days annually.

Both branches now read the actual length of the month the row is for. `period` is
non-null and pinned to the first of its month by `ck_expense_period_first_of_month`, so
the day count is a pure function of a column the expression already reads - the same
anchoring the `custom` branch has always used, and for the same reason: a GENERATED
column must be immutable and cannot call now(). February 2028 has 29 days because the
calendar says so, not because a constant was chosen well.

`monthly_cost` therefore varies month to month for an unchanged plan, and every
historical row is restated by this migration - a 2026 February row that recorded $304.38
reads $280.00 afterwards. That is accepted here because the only data is test data. The
same migration against live data would need a decision this one skips: Met is evaluated
relative to `monthly_cost`, so restating a settled month can flip rows between met and
unmet retroactively, changing what Rollover would have carried.

The expression is dropped and re-added rather than altered in place. The database is
PostgreSQL 16.14, and `ALTER TABLE ... ALTER COLUMN ... SET EXPRESSION` arrived in
PostgreSQL 17 - it is a syntax error here. That rewrites the table, recomputes every row,
and moves the column to the end of the physical column order, which is harmless because
every read is by name. Nothing indexes or constrains `monthly_cost`.

Written by hand: Alembic's autogenerate does not diff a `Computed` expression and emits
an empty upgrade() for this change.

See docs/adr/0012.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "882e753a5321"
down_revision: str | Sequence[str] | None = "8aa3df276438"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Spelled out here rather than imported from app.models.expense: a migration is a
# snapshot of what the schema became at this revision, and one that reads live model
# code stops reproducing history the next time that code changes.
_NEW_EXPR = (
    "\nCASE frequency\n"
    "    WHEN 'daily' THEN cost * EXTRACT(DAY FROM (period + INTERVAL '1 month' - INTERVAL '1 day'))\n"  # noqa: E501
    "    WHEN 'weekly' THEN cost * EXTRACT(DAY FROM (period + INTERVAL '1 month' - INTERVAL '1 day')) / 7\n"  # noqa: E501
    "    WHEN 'monthly' THEN cost\n"
    "    WHEN 'yearly' THEN cost / 12\n"
    "    WHEN 'once' THEN cost\n"
    "    WHEN 'custom' THEN cost / GREATEST(\n"
    "        (EXTRACT(YEAR FROM goal_date) - EXTRACT(YEAR FROM period)) * 12\n"
    "        + (EXTRACT(MONTH FROM goal_date) - EXTRACT(MONTH FROM period)),\n"
    "        1\n"
    "    )\nEND\n"
)

_OLD_EXPR = (
    "\nCASE frequency\n"
    "    WHEN 'daily' THEN cost * 30\n"
    "    WHEN 'weekly' THEN cost * 52.0 / 12\n"
    "    WHEN 'monthly' THEN cost\n"
    "    WHEN 'yearly' THEN cost / 12\n"
    "    WHEN 'once' THEN cost\n"
    "    WHEN 'custom' THEN cost / GREATEST(\n"
    "        (EXTRACT(YEAR FROM goal_date) - EXTRACT(YEAR FROM period)) * 12\n"
    "        + (EXTRACT(MONTH FROM goal_date) - EXTRACT(MONTH FROM period)),\n"
    "        1\n"
    "    )\nEND\n"
)


def _replace_monthly_cost(expression: str) -> None:
    """Swap the generated expression by dropping and re-adding the column."""
    op.drop_column("expenses", "monthly_cost")
    op.add_column(
        "expenses",
        sa.Column(
            "monthly_cost",
            sa.Numeric(precision=12, scale=2),
            sa.Computed(expression, persisted=True),
            nullable=False,
        ),
    )


def upgrade() -> None:
    """Upgrade schema."""
    _replace_monthly_cost(_NEW_EXPR)


def downgrade() -> None:
    """Downgrade schema."""
    _replace_monthly_cost(_OLD_EXPR)
