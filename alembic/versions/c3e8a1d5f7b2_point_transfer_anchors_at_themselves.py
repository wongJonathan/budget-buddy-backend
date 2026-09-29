"""point every Transfer anchor's transfer_id at itself

Revision ID: c3e8a1d5f7b2
Revises: 5d2e9b7a41c3
Create Date: 2026-09-29 12:00:00.000000

Data only. Every row of a Transfer now carries the same `transfer_id` - the anchor's
id - so membership is `transfer_id IS NOT NULL`. The anchor used to leave it null, which
made it look like a row outside any Transfer. Existing anchors are the drain pairs'
fund side: every Pool side already points at one, so the TRANSFER rows still null are
exactly the anchors.

See docs/adr/0016.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3e8a1d5f7b2"
down_revision: str | Sequence[str] | None = "5d2e9b7a41c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade data."""
    op.execute(
        sa.text(
            "UPDATE transactions SET transfer_id = id "
            "WHERE type = 'transfer' AND transfer_id IS NULL"
        )
    )


def downgrade() -> None:
    """Downgrade data."""
    op.execute(sa.text("UPDATE transactions SET transfer_id = NULL WHERE transfer_id = id"))
