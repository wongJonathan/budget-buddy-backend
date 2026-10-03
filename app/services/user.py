import zoneinfo

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget import Budget
from app.models.category import Category
from app.models.user import User
from app.ownership import verify_owned_refs
from app.schemas.user import UserUpdate


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    # Not db.get() - that takes a primary key, and this table's is a UUID. Postgres
    # rejects an email as a UUID outright rather than just failing to match.
    result = await db.scalars(select(User).where(User.email == email))
    # one_or_none rather than first: users.email is UNIQUE, so a second row would mean
    # the constraint is gone, and that should be loud rather than silently picking one.
    return result.one_or_none()


async def update_user(db: AsyncSession, user: User, data: UserUpdate) -> User:
    await verify_owned_refs(db, data, user)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return user


async def delete_user(
    db: AsyncSession,
    user: User,
) -> None:
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


def _is_time_zone(name: str) -> bool:
    try:
        zoneinfo.ZoneInfo(name)
    except zoneinfo.ZoneInfoNotFoundError, ValueError:
        # ValueError covers names that aren't even shaped like a key ("../etc", "").
        return False
    return True


async def record_activity(db: AsyncSession, user: User, time_zone: str | None) -> User:
    user.last_active_at = func.now()
    if time_zone is not None and _is_time_zone(time_zone):
        user.time_zone = time_zone
    await db.commit()
    await db.refresh(user)
    return user
