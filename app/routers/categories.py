from collections.abc import Sequence

from fastapi import APIRouter, status

from app.dependencies import CurrentUser, DbSession, ReadableCategory, WritableCategory
from app.models.category import Category
from app.schemas.category import CategoryCreate, CategoryRead, CategoryUpdate
from app.services import category as category_service

router = APIRouter(prefix="/categories", tags=["categories"])


@router.post("", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
async def create_category(
    data: CategoryCreate, user: CurrentUser, db: DbSession
) -> Category:
    return await category_service.create_category(db, data, user)


@router.get("/{category_id}", response_model=CategoryRead)
async def get_category(category: ReadableCategory) -> Category:
    return category


@router.get("", response_model=list[CategoryRead])
async def get_categories(user: CurrentUser, db: DbSession) -> Sequence[Category]:
    return await category_service.list_categories(db, user)


@router.patch("/{category_id}", response_model=CategoryRead)
async def update_category(
    category: WritableCategory, data: CategoryUpdate, db: DbSession
) -> Category:
    return await category_service.update_category(db, category, data)


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(category: WritableCategory, db: DbSession) -> None:
    await category_service.delete_category(db, category)
