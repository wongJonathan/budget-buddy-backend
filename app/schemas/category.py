import uuid

from pydantic import BaseModel, ConfigDict

from app.schemas.base import CreatableSchema


class CategoryCreate(BaseModel):
    name: str


class CategoryUpdate(BaseModel):
    name: str | None = None


class CategoryRead(CreatableSchema):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
