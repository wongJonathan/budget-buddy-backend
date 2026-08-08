import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BudgetCreate(BaseModel):
    user_id: uuid.UUID
    name: str
    note: str | None = None


class BudgetUpdate(BaseModel):
    name: str | None = None
    note: str | None = None


class BudgetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    note: str | None
    is_deleted: bool
    created_at: datetime
