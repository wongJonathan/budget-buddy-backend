import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.enums import Frequency


class ExpenseCreate(BaseModel):
    budget_id: uuid.UUID
    category_id: uuid.UUID
    name: str
    note: str | None = None
    cost: Decimal
    frequency: Frequency
    amount_saved: Decimal = Decimal(0)
    goal_amount: Decimal | None = None
    goal_date: date | None = None
    period: date


class ExpenseUpdate(BaseModel):
    name: str | None = None
    note: str | None = None
    cost: Decimal | None = None
    frequency: Frequency | None = None
    amount_saved: Decimal | None = None
    goal_amount: Decimal | None = None
    goal_date: date | None = None
    period: date | None = None


class ExpenseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    budget_id: uuid.UUID
    category_id: uuid.UUID
    series_id: uuid.UUID
    name: str
    note: str | None
    cost: Decimal
    frequency: Frequency
    monthly_cost: Decimal
    amount_saved: Decimal
    goal_amount: Decimal | None
    goal_date: date | None
    period: date
    is_deactivated: bool
