from collections.abc import Sequence

from fastapi import APIRouter, status

from app.dependencies import (
    CurrentUser,
    DbSession,
    ReadableBudget,
    ReadableExpense,
    WritableExpense,
)
from app.models.expense import Expense
from app.schemas.expense import ExpenseCreate, ExpenseRead, ExpenseUpdate
from app.schemas.fields import RequestedPeriod, current_period
from app.services import expense as expense_service

router = APIRouter(prefix="/expenses", tags=["expenses"])
budget_expenses_router = APIRouter(prefix="/budgets/{budget_id}", tags=["expenses"])


@router.post("", response_model=ExpenseRead, status_code=status.HTTP_201_CREATED)
async def create_expense(
    data: ExpenseCreate, user: CurrentUser, db: DbSession
) -> Expense:
    return await expense_service.create_expense(db, data, user)


@router.post("/bulk", status_code=status.HTTP_201_CREATED)
async def create_bulk_expenses(
    data: list[ExpenseCreate], user: CurrentUser, db: DbSession
) -> None:
    await expense_service.create_bulk_expenses(db, data, user)


@router.get("/{expense_id}", response_model=ExpenseRead)
async def get_expense(expense: ReadableExpense) -> Expense:
    return expense


@router.patch("/{expense_id}", response_model=ExpenseRead)
async def update_expense(
    expense: WritableExpense, data: ExpenseUpdate, db: DbSession
) -> Expense:
    return await expense_service.update_expense(db, expense, data)


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_expense(expense: WritableExpense, db: DbSession) -> None:
    await expense_service.soft_delete_expense(db, expense)


@budget_expenses_router.get("/expenses", response_model=list[ExpenseRead])
async def list_budget_expenses(
    budget: ReadableBudget,
    db: DbSession,
    period: RequestedPeriod = None,
    include_deleted: bool = False,
) -> Sequence[Expense]:
    return await expense_service.list_budget_expenses(
        db, budget.id, period or current_period(), include_deleted
    )
