"""Transaction payloads.

`expense_id` is optional because Income is a Transaction with no Expense - see
docs/adr/0009. It stays annotated `ExpenseRef`, nested inside the union, so an id
that *is* supplied is still ownership-checked; `verify_owned_refs` skips a null.

`name` is required on the way in for every type, since an income row has nothing
else to identify it by.
"""

import datetime
import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.enums import TransactionType
from app.ownership import ExpenseRef, TransactionRef


class TransactionCreate(BaseModel):
    expense_id: ExpenseRef | None = None
    type: TransactionType
    name: str
    amount: Decimal
    note: str | None = None
    date: datetime.date
    transfer_id: TransactionRef | None = None


class TransactionUpdate(BaseModel):
    type: TransactionType | None = None
    name: str | None = None
    amount: Decimal | None = None
    note: str | None = None
    date: datetime.date | None = None
    transfer_id: TransactionRef | None = None


class TransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    expense_id: uuid.UUID | None
    type: TransactionType
    name: str
    amount: Decimal
    note: str | None
    date: datetime.date
    transfer_id: uuid.UUID | None
    is_deleted: bool
