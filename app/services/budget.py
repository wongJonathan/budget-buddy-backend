import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget import Budget
from app.schemas.budget import BudgetCreate, BudgetUpdate


async def create_budget(db: AsyncSession, data: BudgetCreate) -> Budget:
    budget = Budget(**data.model_dump())
    db.add(budget)
    await db.commit()
    await db.refresh(budget)
    return budget


async def get_budget(db: AsyncSession, budget_id: uuid.UUID) -> Budget | None:
    return await db.get(Budget, budget_id)


async def list_budgets(db: AsyncSession) -> Sequence[Budget]:
    result = await db.execute(select(Budget).where(~Budget.is_deleted))
    return result.scalars().all()


async def update_budget(
    db: AsyncSession, budget_id: uuid.UUID, data: BudgetUpdate
) -> Budget | None:
    budget = await db.get(Budget, budget_id)
    if budget is None:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(budget, field, value)
    await db.commit()
    await db.refresh(budget)
    return budget


async def soft_delete_budget(db: AsyncSession, budget_id: uuid.UUID) -> bool:
    budget = await db.get(Budget, budget_id)
    if budget is None:
        return False
    budget.is_deleted = True
    await db.commit()
    return True
