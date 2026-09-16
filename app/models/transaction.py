import datetime
import uuid
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Numeric
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UUIDPrimaryKeyMixin
from app.models.enums import TransactionType, pg_enum


class Transaction(UUIDPrimaryKeyMixin, Base):
    """Any change to the money available to a User - in, out, or between Pool and fund."""

    __tablename__ = "transactions"
    __table_args__ = (Index("ix_transactions_user_date", "user_id", "date"),)

    # Null expense_id indicates that it's income
    expense_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("expenses.id", ondelete="CASCADE"),
        default=None,
    )
    # Which fund this money moved, recorded here rather than derived through
    # `expense_id -> Expense.savings_id`. Not a duplicate of that column: this is the
    # fund the money *went into*, a historical fact that must never change, while
    # `Expense.savings_id` is the fund a lineage *currently funds* and is re-pointable
    # once Activation takes a reallocation map. Deriving it would rewrite history the
    # moment a lineage was re-pointed, and would also make a balance depend on whether
    # the Expense happened to be soft-deleted. See docs/adr/0011.
    #
    # Non-null on exactly the rows that move a fund - SAVE, SPEND_SAVED, and the fund
    # side of a TRANSFER pair. Null on SPEND, INCOME and a transfer's Pool side.
    savings_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("savings.id", ondelete="SET NULL"),
        default=None,
        index=True,
    )
    type: Mapped[TransactionType] = mapped_column(
        pg_enum(TransactionType, "transaction_type")
    )
    name: Mapped[str] = mapped_column()
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    note: Mapped[str | None] = mapped_column(default=None)
    date: Mapped[datetime.date] = mapped_column(Date)
    is_deleted: Mapped[bool] = mapped_column(default=False, server_default="false")
    transfer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="SET NULL"),
        default=None,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
