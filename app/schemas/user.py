import datetime
import uuid

from pydantic import BaseModel, ConfigDict


class UserCreate(BaseModel):
    display_name: str
    active_budget_id: uuid.UUID | None = None


class UserUpdate(BaseModel):
    display_name: str | None = None
    active_budget_id: uuid.UUID | None = None


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: str
    active_budget_id: uuid.UUID | None
    last_active: datetime.date
