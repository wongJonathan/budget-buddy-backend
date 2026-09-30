from fastapi import APIRouter, status

from app.dependencies import (
    CurrentUser,
    DbSession,
    ReadableBudget,
    ReadableExpense,
    WritableExpense,
)
from app.models.expense import Expense
from app.schemas.expense import (
    ExpenseCreate,
    ExpenseRead,
    ExpenseUpdate,
    ExpenseWithAllocated,
)
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


@router.post("/{expense_id}/restore", response_model=ExpenseRead)
async def restore_expense(expense: ReadableExpense, db: DbSession) -> Expense:
    return await expense_service.restore_expense(expense=expense, db=db)


async def _with_allocated(
    db: DbSession, expenses: list[Expense]
) -> list[ExpenseWithAllocated]:
    totals = await expense_service.allocated(db, (e.id for e in expenses))
    return [
        ExpenseWithAllocated.model_validate(
            {**ExpenseRead.model_validate(e).model_dump(), "allocated": totals[e.id]}
        )
        for e in expenses
    ]


@router.get("/{expense_id}", response_model=ExpenseWithAllocated)
async def get_expense(
    expense: ReadableExpense, db: DbSession
) -> ExpenseWithAllocated:
    [read] = await _with_allocated(db, [expense])
    return read


@budget_expenses_router.get("/expenses", response_model=list[ExpenseWithAllocated])
async def list_budget_expenses(
    budget: ReadableBudget,
    db: DbSession,
    period: RequestedPeriod = None,
    include_deleted: bool = False,
) -> list[ExpenseWithAllocated]:
    expenses = await expense_service.list_budget_expenses(
        db, budget.id, period or current_period(), include_deleted
    )
    return await _with_allocated(db, list(expenses))


@router.patch("/{expense_id}", response_model=ExpenseRead)
async def update_expense(
    expense: WritableExpense, data: ExpenseUpdate, db: DbSession
) -> Expense:
    return await expense_service.update_expense(db, expense, data)


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_expense(expense: WritableExpense, db: DbSession) -> None:
    await expense_service.soft_delete_expense(db, expense)
