import datetime
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget import Budget
from app.models.category import Category
from app.models.user import User
from app.schemas.user import UserUpdate


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


async def update_user(db: AsyncSession, user: User, data: UserUpdate) -> User | None:
    if user is None:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return user


async def delete_user(
    db: AsyncSession,
    user: User,
) -> bool:
    """Erase an account and everything it owns.

    The teardown order is forced by `expenses.category_id`, which is ON DELETE
    RESTRICT so that deleting a mere label can't destroy the Expenses using it. That
    check fires immediately, so a bare `DELETE FROM users` is refused: the cascade
    reaches the user's Categories while their Expenses still point at them. Removing
    the Budgets first takes the Expenses (and their Transactions) with them, which
    frees the Categories to go.

    `auth_events` deliberately survives this - only its `user_id` is nulled, by the
    FK's ON DELETE SET NULL, so the trail outlives the account it describes. See
    docs/adr/0007.
    """
    await db.execute(delete(Budget).where(Budget.user_id == user.id))
    await db.execute(delete(Category).where(Category.user_id == user.id))
    await db.delete(user)
    await db.commit()
    return True


async def update_user_last_active(
    db: AsyncSession,
    user: User,
) -> User | None:
    if user is None:
        return None
    user.last_active = datetime.date.today()
    await db.commit()
    await db.refresh(user)
    return user
