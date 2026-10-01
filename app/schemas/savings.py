import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.schemas.base import CreatableSchema


class SavingsUpdate(BaseModel):
    """The note is all a User can change: the Fund is derived, and closing a Savings is
    what deleting its Expense does (ADR-0014 as amended)."""

    note: str | None = None


class SavingsRead(CreatableSchema):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    note: str | None


class SavingsWithFund(SavingsRead):
    """A Savings with its Fund as of the end of a Period.

    `fund` is `before_period_fund + period_fund`, so for a past Period it is what the
    Savings held when that Period ended, not what it holds today. Movements are placed
    by their Transaction's date.
    """

    before_period_fund: Decimal
    period_fund: Decimal
    fund: Decimal
