"""Transaction payloads.

`type` is `ClientTransactionType`, not the bare enum: `spend_saved` is written by the
server and rejected here, on update as well as create (docs/adr/0011). `transfer` may be
requested, but the server writes its rows, and a PATCH can neither turn a row into one
nor change a Transfer row's type - that is refused in the service, which can see the row
(docs/adr/0016).

`expense_id` is optional because Income is a Transaction with no Expense - see
docs/adr/0009. It stays annotated `ExpenseRef`, nested inside the union, so an id
that *is* supplied is still ownership-checked; `verify_owned_refs` skips a null.

`name` is required on the way in for every type, since an income row has nothing
else to identify it by.
"""

import datetime
import uuid
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.enums import TransactionType
from app.models.transaction import Transaction
from app.ownership import ExpenseRef
from app.schemas.base import CreatableSchema
from app.schemas.fields import ClientTransactionType


class TransactionCreate(BaseModel):
    # `category_id` and `savings_id` are derived from this. On a transfer it is the
    # source, whose fund is drawn on.
    expense_id: ExpenseRef | None = None
    # A transfer's destination; null sends the money to the Pool. See docs/adr/0016.
    to_expense_id: ExpenseRef | None = None

    type: ClientTransactionType
    name: str
    amount: Decimal
    note: str | None = None
    date: datetime.date

    @model_validator(mode="after")
    def _only_income_can_have_no_expense(self) -> TransactionCreate:
        if self.type is not TransactionType.INCOME and self.expense_id is None:
            raise ValueError(
                "A linked expense is required for every transaction type except Income"
            )
        return self

    @model_validator(mode="after")
    def _transfer_shape(self) -> TransactionCreate:
        if self.type is not TransactionType.TRANSFER:
            if self.to_expense_id is not None:
                raise ValueError("to_expense_id is only a transfer's destination")
            return self
        # Negative would run Pool to fund, which is a Save that skips Met.
        if self.amount <= 0:
            raise ValueError("a transfer amount must be positive")
        if self.to_expense_id is not None and self.to_expense_id == self.expense_id:
            raise ValueError("a transfer cannot go to the same Expense it comes from")
        return self


class TransactionUpdate(BaseModel):
    """On any row of a Transfer this edits the whole Transfer, and `expense_id` and
    `to_expense_id` mean its source and destination whichever row was PATCHed."""

    expense_id: ExpenseRef | None = None
    to_expense_id: ExpenseRef | None = None

    type: ClientTransactionType | None = None
    name: str | None = None
    amount: Decimal | None = None
    note: str | None = None
    date: datetime.date | None = None


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
    # The same on every row of one Transfer, anchor included: group rows by this.
    transfer_id: uuid.UUID | None


class TransactionPage(BaseModel):
    items: list[TransactionRead]
    total: int
    limit: int
    offset: int


@dataclass
class TransactionGroup:
    """Groups a transaction
    anchor: source expense
    pool_side: transaction to fund the pool
    save: optional expense to fund from the transfer
    """

    anchor: Transaction
    pool_side: Transaction
    save: Transaction | None

    @property
    def rows(self) -> list[Transaction]:
        return [self.anchor, self.pool_side] + ([self.save] if self.save else [])
