import datetime
import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.enums import TransactionType


class TransactionCreate(BaseModel):
    expense_id: uuid.UUID
    type: TransactionType
    amount: Decimal
    note: str | None = None
    date: datetime.date
    transfer_id: uuid.UUID | None = None


class TransactionUpdate(BaseModel):
    type: TransactionType | None = None
    amount: Decimal | None = None
    note: str | None = None
    date: datetime.date | None = None
    transfer_id: uuid.UUID | None = None


class TransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    expense_id: uuid.UUID
    type: TransactionType
    amount: Decimal
    note: str | None
    date: datetime.date
    transfer_id: uuid.UUID | None
