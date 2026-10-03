"""Rollover: walk a User's active Budget forward to a target Period.

Not implemented yet - this is the seam `tests/services/test_rollover.py` is written
against. See docs/adr/0004, 0010 and 0018, and Rollover / Shortfall / Carried Expense
in CONTEXT.md.
"""

import datetime
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import Frequency
from app.models.expense import Expense
from app.models.rolled_period import RolledPeriod
from app.models.user import User
from app.schemas.fields import first_of_month
from app.services.expense import allocated
from app.services.visibility import live_expenses

COLUMNS_NOT_COPPIED = {
    "id",
    "created_at",
    "updated_at",
    "deleted_at",
    "period",
    "monthly_cost",
}


def _copy_expense(expense: Expense, **overrides: Any) -> Expense:
    fields = {
        attr.key: getattr(expense, attr.key)
        for attr in inspect(Expense).column_attrs
        if attr.key not in COLUMNS_NOT_COPPIED
    }

    return Expense(**(fields | overrides))


async def rollover(db: AsyncSession, user: User, *, through: datetime.date) -> None:
    """Roll `user`'s active Budget forward one Period at a time, up to and including
    `through`, all in one DB transaction.

    The scheduled job passes `current_period()`; tests pass a fixed Period so they
    never depend on the clock.
    """
    # Check if a roll over is needed
    budget_id = user.active_budget_id
    current_period = first_of_month(through)

    rollover_check = (
        await db.execute(
            select(RolledPeriod).where(
                RolledPeriod.budget_id == budget_id,
                RolledPeriod.period == current_period,
            )
        )
    ).scalar()

    if rollover_check is not None:
        print(f"Rollover already completed for {user.id}")
        return

    # Gather all expenses that have not been deleted (deactivated is fine)
    last_expense_period = (
        await db.execute(
            live_expenses()
            .where(
                Expense.budget_id == budget_id,
                Expense.user_id == user.id,
                Expense.period < current_period,
            )
            .order_by(Expense.period.desc())
        )
    ).scalar()

    if last_expense_period is None:
        print(f"No expenses could be found for {user.id}")
        # If no expense can be found, there's nothing to rollover
        return

    last_period = last_expense_period.period
    last_active_expenses = (
        (
            await db.execute(
                live_expenses(include_deleted=False).where(
                    Expense.budget_id == budget_id,
                    Expense.period == last_period,
                )
            )
        )
        .scalars()
        .all()
    )

    expense_ids = [expense.id for expense in last_active_expenses]
    expense_allocations = await allocated(db, expense_ids)

    # Create a new expense that copies the current expenses
    for expense in last_active_expenses:
        allocation = expense_allocations[expense.id]

        if allocation < expense.monthly_cost:
            diff = expense.monthly_cost - allocation
            db.add(
                _copy_expense(
                    expense,
                    frequency=Frequency.ONCE,
                    cost=diff,
                    series_id=None,
                    name=(
                        f"DEBT {expense.name}"
                        if "DEBT" not in expense.name
                        else expense.name
                    ),
                    goal_amount=None,
                    goal_date=None,
                    note="Amount not met from last month",
                    period=current_period,
                )
            )

        if expense.frequency == Frequency.ONCE or (
            expense.goal_date and expense.goal_date < through
        ):
            continue
        db.add(_copy_expense(expense, period=current_period))

    db.add(
        RolledPeriod(
            budget_id=user.active_budget_id,
            period=current_period,
        )
    )
    await db.commit()
    print(f"Rollover completed for {user.id}")
