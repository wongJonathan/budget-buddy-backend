import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UUIDPrimaryKeyMixin
from app.models.enums import CategorySystemType, pg_enum


class Category(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "categories"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column()
    system_type: Mapped[CategorySystemType | None] = mapped_column(
        pg_enum(CategorySystemType, "category_system_type"),
        default=None,
    )
