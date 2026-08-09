import datetime
import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, status

from app.dependencies import DbSession
from app.models.budget import Budget
from app.schemas.budget import (
    BudgetCreate,
    BudgetExpensesRead,
    BudgetRead,
    BudgetUpdate,
)
from app.schemas.expense import ExpenseRead
from app.services import budget as budget_service
from app.services.expense import list_budget_expenses

router = APIRouter(prefix="/budgets", tags=["budgets"])


@router.post("", response_model=BudgetRead, status_code=status.HTTP_201_CREATED)
async def create_budget(data: BudgetCreate, db: DbSession) -> Budget:
    return await budget_service.create_budget(db, data)


@router.get("", response_model=list[BudgetRead])
async def list_budgets(db: DbSession) -> Sequence[Budget]:
    return await budget_service.list_budgets(db)


@router.get("/{budget_id}", response_model=BudgetExpensesRead)
async def get_budget(
    budget_id: uuid.UUID,
    db: DbSession,
    period: str | None = None,
    include_deleted: bool = False,
) -> BudgetExpensesRead:
    try:
        period_date = (
            datetime.date.strptime(period, "%Y-%m") if period else datetime.date.today()
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{period} not right format. Must be in YYYY-MM format",
        ) from ValueError

    budget = await budget_service.get_budget(db, budget_id)
    if budget is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found"
        )

    expenses_sequence = await list_budget_expenses(
        db, budget.id, period_date, include_deleted
    )
    expenses = [ExpenseRead.model_validate(expense) for expense in expenses_sequence]

    return BudgetExpensesRead(
        id=budget.id,
        user_id=budget.user_id,
        name=budget.name,
        note=budget.note,
        is_deleted=budget.is_deleted,
        created_at=budget.created_at,
        expenses=expenses,
    )


@router.patch("/{budget_id}", response_model=BudgetRead)
async def update_budget(
    budget_id: uuid.UUID, data: BudgetUpdate, db: DbSession
) -> Budget:
    budget = await budget_service.update_budget(db, budget_id, data)
    if budget is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found"
        )
    return budget


@router.delete("/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(budget_id: uuid.UUID, db: DbSession) -> None:
    deleted = await budget_service.soft_delete_budget(db, budget_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found"
        )


# I want to create an endpoint to upload json expense data and it creates the budget
@router.post(
    "/json-convert-budget",
    response_model=BudgetRead,
    status_code=status.HTTP_201_CREATED,
)
async def json_convert_budget(
    meta: Annotated[str, Form()], file: Annotated[bytes, File()], db: DbSession
) -> Budget:
    metadata = BudgetCreate.model_validate_json(meta)
    budget = await budget_service.convert_json_to_budget(db, metadata, file)
    return budget
