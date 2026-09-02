"""What Rollover has already done, recorded rather than inferred.

Rollover runs as a scheduled job, so it will be re-run: after a failure, on a retry,
or catching up several months after an outage. It therefore has to recognise its own
prior output, and the obvious cheap test - "does the target Period already have
Expenses?" - reads the wrong signal. `ExpenseCreate.period` is client-set, so a single
user-created row in the target Period would make Rollover skip the whole month and
silently drop every carry-forward. Presence of rows is evidence about the *user*.

One row per (budget, period) Rollover has processed, written in the same transaction
as that period's Expenses, so the record and the rows it describes commit or roll back
together. See docs/adr/0010.
"""

import datetime
import uuid

from sqlalchemy import Date, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UUIDPrimaryKeyMixin


class RolledPeriod(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "rolled_periods"
    __table_args__ = (
        # The idempotency guarantee itself: a second run for the same month loses the
        # insert rather than duplicating a month's worth of carried Expenses. Enforced
        # by the database because two job instances can overlap, and a SELECT-then-INSERT
        # in the service would not survive that.
        UniqueConstraint("budget_id", "period", name="uq_rolled_period_budget_period"),
    )

    budget_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("budgets.id", ondelete="CASCADE"), index=True
    )
    # The Period that was rolled *into*, always the first of its month.
    period: Mapped[datetime.date] = mapped_column(Date)
    rolled_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
