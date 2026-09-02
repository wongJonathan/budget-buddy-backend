import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.enums import Frequency
from app.ownership import BudgetRef, CategoryRef
from app.schemas.fields import CurrentPeriod


class ExpenseCreate(BaseModel):
    budget_id: BudgetRef
    category_id: CategoryRef
    name: str
    note: str | None = None
    cost: Decimal
    frequency: Frequency
    amount_saved: Decimal = Decimal(0)
    goal_amount: Decimal | None = None
    goal_date: date | None = None
    period: CurrentPeriod


class ExpenseUpdate(BaseModel):
    name: str | None = None
    note: str | None = None
    cost: Decimal | None = None
    frequency: Frequency | None = None
    amount_saved: Decimal | None = None
    goal_amount: Decimal | None = None
    goal_date: date | None = None
    period: CurrentPeriod | None = None


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
    is_deleted: bool
