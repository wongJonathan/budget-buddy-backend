"""Guarantees the database makes, not the application.

Both rules here are enforced by Postgres because the application is not the only
thing that writes: a migration, a scheduled job, or a hand-run `UPDATE` can all put
rows in. A validator in `app/schemas` covers the API door only, which is why the
CHECK and the UNIQUE exist underneath it. See docs/adr/0010.
"""

import datetime
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget import Budget
from app.models.category import Category
from app.models.enums import Frequency
from app.models.expense import Expense
from app.models.rolled_period import RolledPeriod
from app.models.user import User
from app.schemas.fields import current_period


async def _budget(db: AsyncSession) -> tuple[Budget, Category, User]:
    user = User(
        display_name="Alice",
        email=f"alice-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
    )
    db.add(user)
    await db.flush()
    budget = Budget(user_id=user.id, name="My Budget")
    category = Category(user_id=user.id, name="Groceries")
    db.add_all([budget, category])
    await db.flush()
    return budget, category, user


def _expense(budget: Budget, category: Category, user: User, period: datetime.date) -> Expense:
    return Expense(
        budget_id=budget.id,
        category_id=category.id,
        user_id=user.id,
        name="Milk",
        cost=Decimal("10.00"),
        frequency=Frequency.MONTHLY,
        period=period,
    )


async def test_a_period_must_be_the_first_of_its_month(db_session: AsyncSession) -> None:
    """The `date.today()` shape that `convert_json_to_budget` used to write.

    It inserted happily and then failed to match anything: `period =` comparisons,
    `uq_expense_budget_period_series`, and Rollover's exact-match reuse all compare
    whole dates. Silently wrong is the failure mode the CHECK exists to convert into
    a loud one.
    """
    budget, category, user = await _budget(db_session)
    db_session.add(_expense(budget, category, user, datetime.date(2026, 8, 17)))

    with pytest.raises(IntegrityError, match="ck_expense_period_first_of_month"):
        await db_session.flush()


async def test_a_first_of_month_period_is_accepted(db_session: AsyncSession) -> None:
    budget, category, user = await _budget(db_session)
    expense = _expense(budget, category, user, current_period())
    db_session.add(expense)
    await db_session.flush()

    assert expense.period.day == 1


async def test_rollover_cannot_record_the_same_period_twice(db_session: AsyncSession) -> None:
    """The idempotency guarantee, in the database rather than in the job.

    Two overlapping runs of the scheduled job would both pass a SELECT-then-INSERT
    check in Python; only the unique constraint actually stops the second one from
    duplicating a month of carried Expenses.
    """
    budget, _, _ = await _budget(db_session)
    period = current_period()
    db_session.add(RolledPeriod(budget_id=budget.id, period=period))
    await db_session.flush()

    db_session.add(RolledPeriod(budget_id=budget.id, period=period))
    with pytest.raises(IntegrityError, match="uq_rolled_period_budget_period"):
        await db_session.flush()


async def test_two_budgets_can_record_the_same_period(db_session: AsyncSession) -> None:
    """The constraint is per budget - Rollover processes each one separately."""
    first, _, _ = await _budget(db_session)
    second, _, _ = await _budget(db_session)
    period = current_period()

    db_session.add_all(
        [
            RolledPeriod(budget_id=first.id, period=period),
            RolledPeriod(budget_id=second.id, period=period),
        ]
    )
    await db_session.flush()
