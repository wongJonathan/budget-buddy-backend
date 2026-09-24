import uuid

from pydantic import BaseModel, ConfigDict

from app.schemas.base import CreatableSchema


class BudgetCreate(BaseModel):
    name: str
    note: str | None = None


class BudgetUpdate(BaseModel):
    name: str | None = None
    note: str | None = None


class BudgetRead(CreatableSchema):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    note: str | None
