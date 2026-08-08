import uuid

from pydantic import BaseModel, ConfigDict

from app.models.enums import CategorySystemType


class CategoryCreate(BaseModel):
    user_id: uuid.UUID
    name: str
    system_type: CategorySystemType | None = None


class CategoryUpdate(BaseModel):
    name: str | None = None
    system_type: CategorySystemType | None = None


class CategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    system_type: CategorySystemType | None
