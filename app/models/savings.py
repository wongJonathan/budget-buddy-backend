"""Money a User has set aside against one Expense lineage.

The row has no balance and no goal, which is deliberate rather than unfinished. Its
balance is derived from the ledger (`app/services/savings.py`), and the goal lives on
the Expense because `goal_date` is referenced by the generated `monthly_cost` column and
a `GENERATED ALWAYS AS` expression can only see its own row.

What is left is the entity's actual job: **durable lineage identity**. ADR-0010 gives a
carried Expense a fresh `series_id` and no link back, so nothing else in the schema can
say "these rows across three Periods are the same real fund". `series_id`
identifies a lineage only until Rollover carries it; `savings_id` outlives that.

Which is why `expenses.savings_id` carries no UNIQUE constraint - many Expense rows, one
per Period, point at one Savings. A UNIQUE would reject next month's row the first time
Rollover ran. "One Savings per Expense" is a statement about concepts, held by only ever
pointing a lineage's rows at the fund they inherited.

See docs/adr/0011.
"""

import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UUIDPrimaryKeyMixin


class Savings(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "savings"

    # User-scoped rather than Budget-scoped: the money is real, and a Budget is a plan.
    # Two Budgets are two competing plans against the same money, so a fund that lived
    # under one would be stranded the moment the other was activated.
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    is_deleted: Mapped[bool] = mapped_column(default=False, server_default="false")
