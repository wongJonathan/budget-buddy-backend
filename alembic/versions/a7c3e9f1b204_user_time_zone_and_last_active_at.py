"""user time zone and last_active_at

Revision ID: a7c3e9f1b204
Revises: d48796019c83
Create Date: 2026-10-03 12:00:00.000000

Hand-written: autogenerate sees `last_active` -> `last_active_at` as a drop and an add,
which would throw away every value. Existing dates become midnight UTC. See
docs/adr/0019 for `time_zone`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c3e9f1b204"
down_revision: str | Sequence[str] | None = "d48796019c83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column("users", "last_active", new_column_name="last_active_at")
    op.alter_column(
        "users",
        "last_active_at",
        type_=sa.DateTime(timezone=True),
        postgresql_using="last_active_at::timestamp AT TIME ZONE 'UTC'",
    )
    op.add_column(
        "users",
        sa.Column(
            "time_zone", sa.String(), server_default=sa.text("'UTC'"), nullable=False
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "time_zone")
    op.alter_column(
        "users",
        "last_active_at",
        type_=sa.Date(),
        postgresql_using="(last_active_at AT TIME ZONE 'UTC')::date",
    )
    op.alter_column("users", "last_active_at", new_column_name="last_active")
