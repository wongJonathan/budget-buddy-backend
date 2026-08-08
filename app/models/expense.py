import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Computed, Date, ForeignKey, Numeric, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UUIDPrimaryKeyMixin
from app.models.enums import Frequency, pg_enum

# `period` (this row's own month) stands in for "today" here, since GENERATED
# columns must be immutable and can't reference now()/CURRENT_DATE. A row is
# always valid for the calendar month `period` marks, so the month-count is
# the same either way — and it naturally increases as Rollover creates each
# month's row closer to goal_date.
_MONTHLY_COST_EXPR = """
CASE frequency
    WHEN 'daily' THEN cost * 30
    WHEN 'weekly' THEN cost * 52.0 / 12
    WHEN 'monthly' THEN cost
    WHEN 'yearly' THEN cost / 12
    WHEN 'once' THEN cost
    WHEN 'custom' THEN cost / GREATEST(
        (EXTRACT(YEAR FROM goal_date) - EXTRACT(YEAR FROM period)) * 12
        + (EXTRACT(MONTH FROM goal_date) - EXTRACT(MONTH FROM period)),
        1
    )
END
"""


class Expense(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "expenses"
    __table_args__ = (
        UniqueConstraint(
            "budget_id", "period", "series_id", name="uq_expense_budget_period_series"
        ),
    )

    budget_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("budgets.id", ondelete="CASCADE")
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categories.id", ondelete="RESTRICT")
    )
    series_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column()
    note: Mapped[str | None] = mapped_column(default=None)
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    frequency: Mapped[Frequency] = mapped_column(
        pg_enum(Frequency, "expense_frequency")
    )
    monthly_cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), Computed(_MONTHLY_COST_EXPR, persisted=True)
    )
    amount_saved: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=0, server_default="0"
    )
    goal_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    goal_date: Mapped[date | None] = mapped_column(Date, default=None)
    period: Mapped[date] = mapped_column(Date)
    is_deactivated: Mapped[bool] = mapped_column(default=False, server_default="false")
