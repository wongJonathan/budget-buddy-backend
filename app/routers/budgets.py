import datetime
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, status

from app.dependencies import CurrentUser, DbSession, OwnedBudget
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
async def create_budget(data: BudgetCreate, user: CurrentUser, db: DbSession) -> Budget:
    return await budget_service.create_budget(db, user, data)


@router.get("/{budget_id}", response_model=BudgetExpensesRead)
async def get_budget(
    budget: OwnedBudget,
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

    expenses_sequence = await list_budget_expenses(
        db, budget.id, period_date, include_deleted
    )
    expenses = [ExpenseRead.model_validate(expense) for expense in expenses_sequence]

    return BudgetExpensesRead(
        **BudgetRead.model_validate(budget).model_dump(),
        expenses=expenses,
    )


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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@router.patch("/{budget_id}", response_model=BudgetRead)
async def update_budget(
    budget: OwnedBudget, data: BudgetUpdate, db: DbSession
) -> Budget:
    return await budget_service.update_budget(db, budget, data)


@router.delete("/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(budget: OwnedBudget, db: DbSession) -> None:
    await budget_service.soft_delete_budget(db, budget)
