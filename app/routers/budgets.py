import uuid
from collections.abc import Sequence

from fastapi import APIRouter, HTTPException, status

from app.dependencies import DbSession
from app.models.budget import Budget
from app.schemas.budget import BudgetCreate, BudgetRead, BudgetUpdate
from app.services import budget as budget_service

router = APIRouter(prefix="/budgets", tags=["budgets"])


@router.post("", response_model=BudgetRead, status_code=status.HTTP_201_CREATED)
async def create_budget(data: BudgetCreate, db: DbSession) -> Budget:
    return await budget_service.create_budget(db, data)


@router.get("", response_model=list[BudgetRead])
async def list_budgets(db: DbSession) -> Sequence[Budget]:
    return await budget_service.list_budgets(db)


@router.get("/{budget_id}", response_model=BudgetRead)
async def get_budget(budget_id: uuid.UUID, db: DbSession) -> Budget:
    budget = await budget_service.get_budget(db, budget_id)
    if budget is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found")
    return budget


@router.patch("/{budget_id}", response_model=BudgetRead)
async def update_budget(budget_id: uuid.UUID, data: BudgetUpdate, db: DbSession) -> Budget:
    budget = await budget_service.update_budget(db, budget_id, data)
    if budget is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found")
    return budget


@router.delete("/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(budget_id: uuid.UUID, db: DbSession) -> None:
    deleted = await budget_service.soft_delete_budget(db, budget_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found")
