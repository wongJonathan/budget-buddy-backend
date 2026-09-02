from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.user import User
from app.schemas.category import CategoryCreate, CategoryUpdate


async def create_category(
    db: AsyncSession, data: CategoryCreate, user: User
) -> Category:
    category = Category(**data.model_dump(), user_id=user.id)
    db.add(category)
    await db.commit()
    await db.refresh(category)
    return category


async def list_categories(db: AsyncSession, user: User) -> Sequence[Category]:
    categories = await db.execute(select(Category).where(Category.user_id == user.id))
    return categories.scalars().all()


async def update_category(
    db: AsyncSession, category: Category, data: CategoryUpdate
) -> Category:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(category, field, value)
    await db.commit()
    await db.refresh(category)
    return category


async def delete_category(db: AsyncSession, category: Category) -> None:
    await db.delete(category)
    await db.commit()
