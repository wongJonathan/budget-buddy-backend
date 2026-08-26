import datetime
import uuid

from sqlalchemy import ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UUIDPrimaryKeyMixin


class Session(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(unique=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=text("now()"))
    expires_at: Mapped[datetime.datetime]
    last_seen_at: Mapped[datetime.datetime] = mapped_column(
        server_default=text("now()")
    )
    # Used for keep track where the user has logged in
    user_agent: Mapped[str | None]
