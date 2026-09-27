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
from app.ownership import CategoryRef, ExpenseRef, SavingsRef, TransactionRef
from app.schemas.base import CreatableSchema
from app.schemas.fields import ClientTransactionType


class TransactionCreate(BaseModel):
    # `category_id` and `savings_id` are derived from this
    expense_id: ExpenseRef | None = None

    type: ClientTransactionType
    name: str
    amount: Decimal
    note: str | None = None
    date: datetime.date
    transfer_id: TransactionRef | None = None

    @model_validator(mode="after")
    def _only_income_can_have_no_expense(self) -> TransactionCreate:
        if self.type is not TransactionType.INCOME and self.expense_id is None:
            raise ValueError(
                "A linked expense is required for every transaction type except Income"
            )
        return self


class TransactionUpdate(BaseModel):
    expense_id: ExpenseRef | None = None

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
    category_id: uuid.UUID | None
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
