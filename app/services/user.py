import datetime
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.user import UserUpdate


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.get(User, user_id)


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    # Not db.get() - that takes a primary key, and this table's is a UUID. Postgres
    # rejects an email as a UUID outright rather than just failing to match.
    result = await db.scalars(select(User).where(User.email == email))
    # one_or_none rather than first: users.email is UNIQUE, so a second row would mean
    # the constraint is gone, and that should be loud rather than silently picking one.
    return result.one_or_none()


async def list_users(db: AsyncSession) -> Sequence[User]:
    result = await db.execute(select(User))
    return result.scalars().all()


async def update_user(db: AsyncSession, user_id: uuid.UUID, data: UserUpdate) -> User | None:
    user = await db.get(User, user_id)
    if user is None:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return user


async def delete_user(db: AsyncSession, user_id: uuid.UUID) -> bool:
    user = await db.get(User, user_id)
    if user is None:
        return False
    await db.delete(user)
    await db.commit()
    return True


async def update_user_last_active(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    user = await db.get(User, user_id)
    if user is None:
        return None
    user.last_active = datetime.date.today()
    await db.commit()
    await db.refresh(user)
    return user
