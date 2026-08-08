import uuid
from collections.abc import Sequence

from fastapi import APIRouter, HTTPException, status

from app.dependencies import DbSession
from app.models.category import Category
from app.schemas.category import CategoryCreate, CategoryRead, CategoryUpdate
from app.services import category as category_service

router = APIRouter(prefix="/categories", tags=["categories"])


@router.post("", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
async def create_category(data: CategoryCreate, db: DbSession) -> Category:
    return await category_service.create_category(db, data)


@router.get("", response_model=list[CategoryRead])
async def list_categories(db: DbSession) -> Sequence[Category]:
    return await category_service.list_categories(db)


@router.get("/{category_id}", response_model=CategoryRead)
async def get_category(category_id: uuid.UUID, db: DbSession) -> Category:
    category = await category_service.get_category(db, category_id)
    if category is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    return category


@router.patch("/{category_id}", response_model=CategoryRead)
async def update_category(category_id: uuid.UUID, data: CategoryUpdate, db: DbSession) -> Category:
    category = await category_service.update_category(db, category_id, data)
    if category is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    return category


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(category_id: uuid.UUID, db: DbSession) -> None:
    deleted = await category_service.delete_category(db, category_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
