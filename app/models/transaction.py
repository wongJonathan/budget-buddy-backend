import datetime
import uuid
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UUIDPrimaryKeyMixin
from app.models.enums import TransactionType, pg_enum


class Transaction(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "transactions"

    expense_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("expenses.id", ondelete="CASCADE")
    )
    type: Mapped[TransactionType] = mapped_column(pg_enum(TransactionType, "transaction_type"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    note: Mapped[str | None] = mapped_column(default=None)
    # `datetime` module (not `from datetime import date`) avoids this field's own
    # name shadowing the type in PEP 649 deferred annotation evaluation.
    date: Mapped[datetime.date] = mapped_column(Date)
    transfer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), default=None
    )
