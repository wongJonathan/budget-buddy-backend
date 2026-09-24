"""Transaction payloads.

`type` is `ClientTransactionType`, not the bare enum: `spend_saved` and `transfer` are
written by the server and rejected here, on update as well as create (docs/adr/0011).

`expense_id` is optional because Income is a Transaction with no Expense - see
docs/adr/0009. It stays annotated `ExpenseRef`, nested inside the union, so an id
that *is* supplied is still ownership-checked; `verify_owned_refs` skips a null.

`name` is required on the way in for every type, since an income row has nothing
else to identify it by.
"""

import datetime
import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.enums import TransactionType
from app.ownership import ExpenseRef, TransactionRef
from app.schemas.base import CreatableSchema
from app.schemas.fields import ClientTransactionType


class TransactionCreate(BaseModel):
    expense_id: ExpenseRef | None = None
    type: ClientTransactionType
    name: str
    amount: Decimal
    note: str | None = None
    date: datetime.date
    transfer_id: TransactionRef | None = None

    @model_validator(mode="after")
    def _save_needs_an_expense(self) -> TransactionCreate:
        """A fund belongs to an Expense lineage, so there is nowhere for a bare Save to
        go - and `open_savings` would have no row to stamp the new `savings_id` on."""
        if self.type is TransactionType.SAVE and self.expense_id is None:
            raise ValueError("a save must name the expense whose savings it funds")
        return self


class TransactionUpdate(BaseModel):
    type: ClientTransactionType | None = None
    name: str | None = None
    amount: Decimal | None = None
    note: str | None = None
    date: datetime.date | None = None
    transfer_id: TransactionRef | None = None


class TransactionRead(CreatableSchema):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    expense_id: uuid.UUID | None
    savings_id: uuid.UUID | None
    type: TransactionType
    name: str
    amount: Decimal
    note: str | None
    date: datetime.date
    transfer_id: uuid.UUID | None


class TransactionPage(BaseModel):
    items: list[TransactionRead]
    total: int
    limit: int
    offset: int
