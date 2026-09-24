from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, status

from app.dependencies import CurrentUser, DbSession, ReadableBudget, WritableBudget
from app.models.budget import Budget
from app.schemas.budget import (
    BudgetCreate,
    BudgetRead,
    BudgetUpdate,
)
from app.services import budget as budget_service

router = APIRouter(prefix="/budgets", tags=["budgets"])


@router.post("", response_model=BudgetRead, status_code=status.HTTP_201_CREATED)
async def create_budget(data: BudgetCreate, user: CurrentUser, db: DbSession) -> Budget:
    return await budget_service.create_budget(db, user, data)


@router.get("/{budget_id}", response_model=BudgetRead)
async def get_budget(
    budget: ReadableBudget,
) -> Budget:
    return budget


@router.post(
    "/json-convert-budget",
    response_model=BudgetRead,
    status_code=status.HTTP_201_CREATED,
)
async def json_convert_budget(
    meta: Annotated[str, Form()],
    file: Annotated[bytes, File()],
    user: CurrentUser,
    db: DbSession,
) -> Budget:
    try:
        metadata = BudgetCreate.model_validate_json(meta)
        budget = await budget_service.convert_json_to_budget(db, metadata, file, user)
        return budget
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.patch("/{budget_id}", response_model=BudgetRead)
async def update_budget(budget: WritableBudget, data: BudgetUpdate, db: DbSession) -> Budget:
    return await budget_service.update_budget(db, budget, data)


@router.delete("/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(budget: WritableBudget, user: CurrentUser, db: DbSession) -> None:
    await budget_service.soft_delete_budget(db, budget, user)
