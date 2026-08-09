import datetime
import uuid
from collections.abc import Sequence

from sqlalchemy import extract, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.expense import Expense
from app.schemas.expense import ExpenseCreate, ExpenseUpdate


async def create_expense(db: AsyncSession, data: ExpenseCreate) -> Expense:
    expense = Expense(**data.model_dump())
    db.add(expense)
    await db.commit()
    await db.refresh(expense)
    return expense


async def get_expense(db: AsyncSession, expense_id: uuid.UUID) -> Expense | None:
    return await db.get(Expense, expense_id)


async def list_expenses(db: AsyncSession) -> Sequence[Expense]:
    result = await db.execute(select(Expense).where(~Expense.is_deactivated))
    return result.scalars().all()


async def update_expense(
    db: AsyncSession, expense_id: uuid.UUID, data: ExpenseUpdate
) -> Expense | None:
    expense = await db.get(Expense, expense_id)
    if expense is None:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(expense, field, value)
    await db.commit()
    await db.refresh(expense)
    return expense


async def soft_delete_expense(db: AsyncSession, expense_id: uuid.UUID) -> bool:
    expense = await db.get(Expense, expense_id)
    if expense is None:
        return False
    expense.is_deactivated = True
    await db.commit()
    return True


async def list_budget_expenses(
    db: AsyncSession, budget_id: uuid.UUID, period: datetime.date, include_deleted: bool
) -> Sequence[Expense]:
    conditions = [
        Expense.budget_id == budget_id,
        extract("year", Expense.period) == period.year,
        extract("month", Expense.period) == period.month,
    ]

    if include_deleted is False:
        conditions.append(~Expense.is_deactivated)

    result = await db.execute(select(Expense).where(*conditions))

    return result.scalars().all()
