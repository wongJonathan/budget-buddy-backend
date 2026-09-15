import datetime
import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.expense import Expense
from app.models.user import User
from app.ownership import verify_owned_refs
from app.schemas.expense import ExpenseCreate, ExpenseUpdate
from app.schemas.fields import current_period
from app.services import savings as savings_service
from app.services.visibility import live_expenses


class ClosedPeriod(AppError):
    """An edit or delete aimed at an Expense outside the open Period.

    409 rather than 404: the row exists and is the caller's. What is refused is
    rewriting a month Rollover may already have acted on - a late change to a
    historical `cost` silently changes a shortfall that was already carried, and
    deleting a historical row would drain a Savings that later Periods still use
    (docs/adr/0011).
    """

    def __init__(self, period: datetime.date) -> None:
        super().__init__(
            f"expense belongs to a closed period ({period.isoformat()}); only the "
            f"current one ({current_period().isoformat()}) can be edited or deleted",
            status_code=409,
        )


def _require_open_period(expense: Expense) -> None:
    """Guard for the write paths that take an existing row.

    `CurrentPeriod` on the schema constrains *where a row is written to*; this
    constrains *which rows may be touched at all*. They are different rules, and
    only the first is expressible as field validation - an update that omits
    `period` never reaches it.
    """
    if expense.period != current_period():
        raise ClosedPeriod(expense.period)


async def create_expense(db: AsyncSession, data: ExpenseCreate, user: User) -> Expense:
    await verify_owned_refs(db, data, user)
    expense = Expense(**data.model_dump(), user_id=user.id)
    db.add(expense)
    await db.commit()
    await db.refresh(expense)
    return expense


async def create_bulk_expenses(db: AsyncSession, data: list[ExpenseCreate], user: User) -> None:
    for expense_data in data:
        await verify_owned_refs(db, expense_data, user)
        expense = Expense(**expense_data.model_dump(), user_id=user.id)
        db.add(expense)
    await db.commit()


async def get_expense(
    db: AsyncSession, expense_id: uuid.UUID, user_id: uuid.UUID
) -> Expense | None:
    # Not db.get(): hidden by a deleted Budget counts as gone, and a soft-deleted
    # Expense must 404 on direct id too.
    result = await db.scalars(
        live_expenses().where(Expense.id == expense_id, Expense.user_id == user_id)
    )
    return result.one_or_none()


async def update_expense(db: AsyncSession, expense: Expense, data: ExpenseUpdate) -> Expense:
    _require_open_period(expense)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(expense, field, value)
    await db.commit()
    await db.refresh(expense)
    return expense


async def soft_delete_expense(db: AsyncSession, expense: Expense) -> None:
    """Delete the Expense, and close the fund it was the live intention for.

    Deleting the current-Period row is the user saying they are not doing this any more,
    so the lineage's fund is drained back to the Pool and closed - money set aside for a
    plan that no longer exists belongs back in the Pool, not stranded behind a deleted
    row (docs/adr/0011). A historical row never reaches here: `_require_open_period`
    refuses it, which is what stops one month's tidy-up destroying a year of savings.
    """
    _require_open_period(expense)
    await savings_service.close_savings(db, expense)
    expense.is_deleted = True
    await db.commit()


async def list_budget_expenses(
    db: AsyncSession, budget_id: uuid.UUID, period: datetime.date, include_deleted: bool
) -> Sequence[Expense]:
    query = live_expenses(include_deleted=include_deleted).where(
        Expense.budget_id == budget_id,
        Expense.period == period,
    )
    result = await db.execute(query)

    return result.scalars().all()
