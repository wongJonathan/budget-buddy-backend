import datetime
import ipaddress
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import DateTime, func, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class Base(DeclarativeBase):
    type_annotation_map = {
        datetime.datetime: DateTime(timezone=True),
        IPAddress: INET,
        dict[str, Any]: JSONB,
    }


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


class CreatableModel(UUIDPrimaryKeyMixin):
    __mapper_args__ = {"eager_defaults": True}
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column()


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
