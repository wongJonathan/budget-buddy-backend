import datetime
import uuid
from collections.abc import Sequence

from sqlalchemy import extract
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.expense import Expense
from app.schemas.expense import ExpenseCreate, ExpenseUpdate
from app.services.visibility import live_expenses


async def create_expense(db: AsyncSession, data: ExpenseCreate) -> Expense:
    expense = Expense(**data.model_dump())
    db.add(expense)
    await db.commit()
    await db.refresh(expense)
    return expense


async def create_bulk_expenses(db: AsyncSession, data: list[ExpenseCreate]) -> None:
    for expense_data in data:
        expense = Expense(**expense_data.model_dump())
        db.add(expense)
    await db.commit()


async def get_expense(db: AsyncSession, expense_id: uuid.UUID) -> Expense | None:
    # Not db.get(): hidden by a deleted Budget counts as gone, and a soft-deleted
    # Expense must 404 on direct id too.
    result = await db.scalars(live_expenses().where(Expense.id == expense_id))
    return result.one_or_none()


async def list_expenses(db: AsyncSession) -> Sequence[Expense]:
    result = await db.execute(live_expenses())
    return result.scalars().all()


async def update_expense(
    db: AsyncSession, expense_id: uuid.UUID, data: ExpenseUpdate
) -> Expense | None:
    expense = await get_expense(db, expense_id)
    if expense is None:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(expense, field, value)
    await db.commit()
    await db.refresh(expense)
    return expense


async def soft_delete_expense(db: AsyncSession, expense_id: uuid.UUID) -> bool:
    expense = await get_expense(db, expense_id)
    if expense is None:
        return False
    expense.is_deleted = True
    await db.commit()
    return True


async def list_budget_expenses(
    db: AsyncSession, budget_id: uuid.UUID, period: datetime.date, include_deleted: bool
) -> Sequence[Expense]:
    query = live_expenses(include_deleted=include_deleted).where(
        Expense.budget_id == budget_id,
        extract("year", Expense.period) == period.year,
        extract("month", Expense.period) == period.month,
    )
    result = await db.execute(query)

    return result.scalars().all()
