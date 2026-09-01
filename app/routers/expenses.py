from collections.abc import Sequence

from fastapi import APIRouter, HTTPException, status

from app.dependencies import CurrentUser, DbSession, OwnedExpense
from app.models.expense import Expense
from app.schemas.expense import ExpenseCreate, ExpenseRead, ExpenseUpdate
from app.services import expense as expense_service

router = APIRouter(prefix="/expenses", tags=["expenses"])


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


@router.get("", response_model=list[ExpenseRead])
async def list_expenses(user: CurrentUser, db: DbSession) -> Sequence[Expense]:
    return await expense_service.list_expenses(db, user)


@router.get("/{expense_id}", response_model=ExpenseRead)
async def get_expense(expense: OwnedExpense) -> Expense:
    return expense


@router.patch("/{expense_id}", response_model=ExpenseRead)
async def update_expense(
    expense: OwnedExpense, data: ExpenseUpdate, db: DbSession
) -> Expense:
    updated_expense = await expense_service.update_expense(db, expense, data)
    if updated_expense is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found"
        )
    return updated_expense


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_expense(expense: OwnedExpense, db: DbSession) -> None:
    deleted = await expense_service.soft_delete_expense(db, expense)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found"
        )
