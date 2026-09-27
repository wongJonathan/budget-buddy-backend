import datetime
import uuid
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Numeric, case, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, column_property, mapped_column

from app.database import Base, CreatableModel
from app.models.enums import TransactionType, pg_enum
from app.models.expense import Expense


class Transaction(CreatableModel, Base):
    """Any change to the money available to a User - in, out, or between Pool and fund."""

    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_user_date_created", "user_id", "date", "created_at"),
    )

    # Null expense_id indicates that it's income
    expense_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("expenses.id", ondelete="CASCADE"),
        default=None,
    )
    category_id: Mapped[uuid.UUID | None] = column_property(
        select(Expense.category_id)
        .where(Expense.id == expense_id)
        .correlate_except(Expense)
        .scalar_subquery()
    )
    type: Mapped[TransactionType] = mapped_column(
        pg_enum(TransactionType, "transaction_type")
    )
    savings_id: Mapped[uuid.UUID | None] = column_property(
        case(
            (
                type.in_(
                    [
                        TransactionType.SAVE,
                        TransactionType.SPEND_SAVED,
                        TransactionType.TRANSFER,
                    ]
                ),
                select(Expense.savings_id)
                .where(Expense.id == expense_id)
                .correlate_except(Expense)
                .scalar_subquery(),
            ),
            else_=None,
        )
    )
    name: Mapped[str] = mapped_column()
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    note: Mapped[str | None] = mapped_column(default=None)
    date: Mapped[datetime.date] = mapped_column(Date)
    transfer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="SET NULL"),
        default=None,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
