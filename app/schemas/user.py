import datetime
import uuid

from pydantic import BaseModel, ConfigDict

from app.ownership import BudgetRef
from app.schemas.budget import BudgetRead


class UserUpdate(BaseModel):
    display_name: str | None = None
    # A user may only point their active budget at one of their own.
    active_budget_id: BudgetRef | None = None


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: str
    active_budget_id: uuid.UUID | None
    last_active: datetime.date


class UserWithBudgetsRead(UserRead):
    budgets: list[BudgetRead]
