import datetime
import uuid

from sqlalchemy import Date, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UUIDPrimaryKeyMixin


class User(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "users"

    display_name: Mapped[str] = mapped_column()
    active_budget_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("budgets.id", ondelete="SET NULL"),
        default=None,
    )
    last_active: Mapped[datetime.date] = mapped_column(
        Date, server_default=text("now()")
    )
