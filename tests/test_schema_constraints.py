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


# ---------------------------------------------------------------------------
# monthly_cost: the GENERATED column
#
# Postgres computes this one, so it is a guarantee of the database in the same
# sense the CHECK above is. See docs/adr/0012 for why the day count comes from
# the row's own `period` rather than from a constant.
#
# Dates here are absolute rather than `current_period()`-relative: the whole
# point is that a specific calendar month has a specific number of days, and a
# relative date would make the expected values move with the clock.
# ---------------------------------------------------------------------------


async def _monthly_cost(
    db: AsyncSession,
    budget: Budget,
    category: Category,
    user: User,
    *,
    cost: str,
    frequency: Frequency,
    period: datetime.date,
) -> Decimal:
    """Insert one Expense and read back what Postgres computed for it."""
    expense = Expense(
        budget_id=budget.id,
        category_id=category.id,
        user_id=user.id,
        name="Milk",
        cost=Decimal(cost),
        frequency=frequency,
        period=period,
    )
    db.add(expense)
    await db.flush()
    await db.refresh(expense)
    return expense.monthly_cost


@pytest.mark.parametrize(
    ("period", "days", "expected"),
    [
        (datetime.date(2027, 2, 1), 28, Decimal("280.00")),
        (datetime.date(2027, 3, 1), 31, Decimal("310.00")),
        # The leap case the old `cost * 30` could not express at all.
        (datetime.date(2028, 2, 1), 29, Decimal("290.00")),
    ],
)
async def test_a_daily_expense_costs_the_days_in_its_own_period(
    db_session: AsyncSession, period: datetime.date, days: int, expected: Decimal
) -> None:
    """$10/day is not a fixed $300 a month - it is $10 times however long the month is."""
    budget, category, user = await _budget(db_session)

    value = await _monthly_cost(
        db_session,
        budget,
        category,
        user,
        cost="10.00",
        frequency=Frequency.DAILY,
        period=period,
    )

    assert value == expected == Decimal(10 * days).quantize(Decimal("0.01"))


@pytest.mark.parametrize("period", [datetime.date(2027, 2, 1), datetime.date(2027, 3, 1)])
async def test_daily_and_weekly_agree_for_the_same_underlying_expense(
    db_session: AsyncSession, period: datetime.date
) -> None:
    """$10/day and $70/week are one plan said two ways, so they must cost the same.

    Asserted as an equality rather than against two literals on purpose. The literals
    would still pass if someone reintroduced a constant into one branch and happened to
    pick a matching one; this cannot, because both sides read the same day count. Under
    the old pairing these disagreed by 1.4% - $300.00 against $303.33.
    """
    budget, category, user = await _budget(db_session)

    daily = await _monthly_cost(
        db_session,
        budget,
        category,
        user,
        cost="10.00",
        frequency=Frequency.DAILY,
        period=period,
    )
    weekly = await _monthly_cost(
        db_session,
        budget,
        category,
        user,
        cost="70.00",
        frequency=Frequency.WEEKLY,
        period=period,
    )

    assert daily == weekly


async def test_a_weekly_expense_need_not_land_on_whole_weeks(db_session: AsyncSession) -> None:
    """March is 4.43 weeks, and `weekly` is a rate rather than a schedule.

    The schema has no anchor weekday, so counting actual occurrences ("how many Mondays
    in March") is not expressible and would be a recurrence rule rather than a better
    conversion. February is the only month that divides evenly.
    """
    budget, category, user = await _budget(db_session)

    value = await _monthly_cost(
        db_session,
        budget,
        category,
        user,
        cost="10.00",
        frequency=Frequency.WEEKLY,
        period=datetime.date(2027, 3, 1),
    )

    assert value == Decimal("44.29")  # 10 * 31 / 7 = 44.2857..., to the column's 2dp
