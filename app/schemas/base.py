from datetime import datetime

from pydantic import BaseModel


class CreatableSchema(BaseModel):
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
