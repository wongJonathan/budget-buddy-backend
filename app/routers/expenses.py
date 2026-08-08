import uuid
from collections.abc import Sequence

from fastapi import APIRouter, HTTPException, status

from app.dependencies import DbSession
from app.models.expense import Expense
from app.schemas.expense import ExpenseCreate, ExpenseRead, ExpenseUpdate
from app.services import expense as expense_service

router = APIRouter(prefix="/expenses", tags=["expenses"])


@router.post("", response_model=ExpenseRead, status_code=status.HTTP_201_CREATED)
async def create_expense(data: ExpenseCreate, db: DbSession) -> Expense:
    return await expense_service.create_expense(db, data)


@router.get("", response_model=list[ExpenseRead])
async def list_expenses(db: DbSession) -> Sequence[Expense]:
    return await expense_service.list_expenses(db)


@router.get("/{expense_id}", response_model=ExpenseRead)
async def get_expense(expense_id: uuid.UUID, db: DbSession) -> Expense:
    expense = await expense_service.get_expense(db, expense_id)
    if expense is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")
    return expense


@router.patch("/{expense_id}", response_model=ExpenseRead)
async def update_expense(expense_id: uuid.UUID, data: ExpenseUpdate, db: DbSession) -> Expense:
    expense = await expense_service.update_expense(db, expense_id, data)
    if expense is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")
    return expense


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_expense(expense_id: uuid.UUID, db: DbSession) -> None:
    deleted = await expense_service.soft_delete_expense(db, expense_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")
