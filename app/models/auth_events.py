import datetime
import uuid
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, IPAddress


class AuthEvents(Base):
    __tablename__ = "auth_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime.datetime] = mapped_column(server_default=text("now()"))
    event_type: Mapped[str] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    email: Mapped[str | None]
    ip: Mapped[IPAddress | None]
    user_agent: Mapped[str | None]
    session_id: Mapped[uuid.UUID | None]
    detail: Mapped[dict[str, Any] | None] = mapped_column(MutableDict.as_mutable(JSONB))

    __table_args__ = (
        Index("ix_auth_events_user_time", "user_id", occurred_at.desc()),
        Index("ix_auth_events_email_time", "email", occurred_at.desc()),
        Index("ix_auth_events_type_time", "event_type", occurred_at.desc()),
    )
