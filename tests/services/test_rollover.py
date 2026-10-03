"""Rollover: successors, Shortfall carries, and the record of what was rolled.

Against a real Postgres. Rollover leans on what the database decides: `monthly_cost` is
a GENERATED column that differs month to month, Allocated is a sum over the ledger, and
idempotency is a row in `rolled_periods` (docs/adr/0004, 0010, 0018).

Rows are seeded directly rather than through the services, because every service refuses
to write outside the open Period (docs/adr/0017) and Rollover reads closed ones. Every
Period here is fixed and passed to `rollover` as `through`, so nothing depends on today.
"""

import datetime
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget import Budget
from app.models.category import Category
from app.models.enums import Frequency, TransactionType
from app.models.expense import Expense
from app.models.rolled_period import RolledPeriod
from app.models.savings import Savings
from app.models.transaction import Transaction
from app.models.user import User
from app.services import expense as expense_service
from app.services.rollover import rollover

JAN = datetime.date(2026, 1, 1)
FEB = datetime.date(2026, 2, 1)
MAR = datetime.date(2026, 3, 1)
APR = datetime.date(2026, 4, 1)

CARRY_NOTE = "Amount not met from last month"


class InjectedFailure(Exception):
    pass


@dataclass
class Ctx:
    user: User
    budget_id: uuid.UUID
    category_id: uuid.UUID


async def _setup(db: AsyncSession) -> Ctx:
    """A User whose active Budget has one Category and no Expenses yet."""
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

    user.active_budget_id = budget.id
    await db.commit()
    return Ctx(user=user, budget_id=budget.id, category_id=category.id)


async def _expense(
    db: AsyncSession,
    ctx: Ctx,
    *,
    name: str = "Groceries",
    cost: str = "400.00",
    frequency: Frequency = Frequency.MONTHLY,
    period: datetime.date = JAN,
    **fields: Any,
) -> Expense:
    fields.setdefault("budget_id", ctx.budget_id)
    expense = Expense(
        category_id=ctx.category_id,
        user_id=ctx.user.id,
        name=name,
        cost=Decimal(cost),
        frequency=frequency,
        period=period,
        **fields,
    )
    db.add(expense)
    await db.commit()
    return expense


async def _fund(db: AsyncSession, ctx: Ctx, expense: Expense) -> uuid.UUID:
    """Give `expense` a Savings, the way its first Save would have."""
    savings = Savings(user_id=ctx.user.id)
    db.add(savings)
    await db.flush()
    expense.savings_id = savings.id
    await db.commit()
    return savings.id


async def _pay(
    db: AsyncSession,
    ctx: Ctx,
    expense: Expense | None,
    type_: TransactionType,
    amount: str,
    *,
    deleted: bool = False,
) -> None:
    """A Transaction dated inside `expense`'s own (closed) Period."""
    db.add(
        Transaction(
            user_id=ctx.user.id,
            expense_id=expense.id if expense else None,
            type=type_,
            name=f"{type_.value} {amount}",
            amount=Decimal(amount),
            date=expense.period if expense else JAN,
            deleted_at=func.now() if deleted else None,
        )
    )
    await db.commit()


async def _rows(db: AsyncSession, budget_id: uuid.UUID, period: datetime.date) -> list[Expense]:
    result = await db.scalars(
        select(Expense)
        .where(Expense.budget_id == budget_id, Expense.period == period)
        .order_by(Expense.name, Expense.cost)
    )
    return list(result.all())


def _shape(rows: list[Expense]) -> list[tuple[str, Frequency, Decimal]]:
    return sorted((e.name, e.frequency, e.cost) for e in rows)


async def _rolled(db: AsyncSession, budget_id: uuid.UUID) -> set[datetime.date]:
    result = await db.scalars(
        select(RolledPeriod.period).where(RolledPeriod.budget_id == budget_id)
    )
    return set(result.all())


def _carries(rows: list[Expense], *sources: Expense) -> list[Expense]:
    """The rows in a Period that are not a successor of any of `sources`."""
    lineages = {s.series_id for s in sources}
    return [e for e in rows if e.series_id not in lineages]


# ---------------------------------------------------------------------------
# successors
# ---------------------------------------------------------------------------


async def test_a_recurring_expense_gets_a_successor_carrying_every_plan_field(
    db_session: AsyncSession,
) -> None:
    """Everything but the row's identity, its timestamps and its Period is the same
    plan continuing - `savings_id` included, or next month's Saves open a second fund.
    """
    ctx = await _setup(db_session)
    source = await _expense(
        db_session,
        ctx,
        note="weekly shop",
        goal_amount=Decimal("1000.00"),
        goal_date=datetime.date(2026, 12, 31),
    )
    savings_id = await _fund(db_session, ctx, source)
    await _pay(db_session, ctx, source, TransactionType.SPEND, "400.00")

    await rollover(db_session, ctx.user, through=FEB)

    (successor,) = await _rows(db_session, ctx.budget_id, FEB)
    assert successor.id != source.id
    assert successor.period == FEB
    assert successor.deleted_at is None
    assert {
        "budget_id": successor.budget_id,
        "category_id": successor.category_id,
        "series_id": successor.series_id,
        "savings_id": successor.savings_id,
        "user_id": successor.user_id,
        "name": successor.name,
        "note": successor.note,
        "cost": successor.cost,
        "frequency": successor.frequency,
        "goal_amount": successor.goal_amount,
        "goal_date": successor.goal_date,
    } == {
        "budget_id": ctx.budget_id,
        "category_id": ctx.category_id,
        "series_id": source.series_id,
        "savings_id": savings_id,
        "user_id": ctx.user.id,
        "name": "Groceries",
        "note": "weekly shop",
        "cost": Decimal("400.00"),
        "frequency": Frequency.MONTHLY,
        "goal_amount": Decimal("1000.00"),
        "goal_date": datetime.date(2026, 12, 31),
    }
    assert await _rolled(db_session, ctx.budget_id) == {FEB}


async def test_monthly_cost_is_recomputed_for_the_new_period_not_copied(
    db_session: AsyncSession,
) -> None:
    """The same daily plan costs 31 days in January and 28 in February (docs/adr/0012)."""
    ctx = await _setup(db_session)
    source = await _expense(db_session, ctx, cost="10.00", frequency=Frequency.DAILY)
    assert source.monthly_cost == Decimal("310.00")

    await rollover(db_session, ctx.user, through=FEB)
    rows = await _rows(db_session, ctx.budget_id, FEB)
    print(rows)
    (successor,) = [e for e in rows if e.series_id == source.series_id]
    assert successor.monthly_cost == Decimal("280.00")


async def test_a_met_once_expense_and_a_deleted_expense_roll_into_nothing(
    db_session: AsyncSession,
) -> None:
    """`once` never recurs, and Met leaves no Shortfall. A deleted Expense ended its
    lineage, so it is neither succeeded nor carried, however unmet it was."""
    ctx = await _setup(db_session)
    once = await _expense(db_session, ctx, name="Gift", cost="50.00", frequency=Frequency.ONCE)
    await _pay(db_session, ctx, once, TransactionType.SPEND, "50.00")
    await _expense(db_session, ctx, name="Gym", cost="30.00", deleted_at=func.now())

    await rollover(db_session, ctx.user, through=FEB)

    assert await _rows(db_session, ctx.budget_id, FEB) == []


async def test_a_custom_expense_past_its_goal_date_gets_no_successor_but_still_carries(
    db_session: AsyncSession,
) -> None:
    """Past `goal_date` the GENERATED expression divides by 1 and would bill the whole
    `cost` every month. The goal is over; only what it fell short by moves on."""
    ctx = await _setup(db_session)
    source = await _expense(
        db_session,
        ctx,
        name="Laptop",
        cost="1200.00",
        frequency=Frequency.CUSTOM,
        goal_date=datetime.date(2026, 1, 31),
    )
    await _pay(db_session, ctx, source, TransactionType.SAVE, "1000.00")

    await rollover(db_session, ctx.user, through=FEB)

    assert _shape(await _rows(db_session, ctx.budget_id, FEB)) == [
        ("DEBT Laptop", Frequency.ONCE, Decimal("200.00")),
    ]


async def test_a_custom_expense_before_its_goal_date_still_gets_a_successor(
    db_session: AsyncSession,
) -> None:
    """The control for the test above: the cut-off is the goal date, not `custom`."""
    ctx = await _setup(db_session)
    source = await _expense(
        db_session,
        ctx,
        name="Laptop",
        cost="1200.00",
        frequency=Frequency.CUSTOM,
        goal_date=datetime.date(2026, 6, 30),
    )
    await _pay(
        db_session,
        ctx,
        source,
        TransactionType.SAVE,
        source.monthly_cost.to_eng_string(),
    )

    await rollover(db_session, ctx.user, through=FEB)

    (successor,) = await _rows(db_session, ctx.budget_id, FEB)
    assert successor.series_id == source.series_id


async def test_a_draft_budget_is_not_rolled(db_session: AsyncSession) -> None:
    """Only the active Budget is live. A draft is a plan for later, not this month's."""
    ctx = await _setup(db_session)
    draft = Budget(user_id=ctx.user.id, name="Draft")
    db_session.add(draft)
    await db_session.commit()
    await _expense(db_session, ctx, budget_id=draft.id)

    await rollover(db_session, ctx.user, through=FEB)

    assert await _rows(db_session, draft.id, FEB) == []
    assert await _rolled(db_session, draft.id) == set()


# ---------------------------------------------------------------------------
# idempotency and where the walk starts
# ---------------------------------------------------------------------------


async def test_a_period_already_recorded_as_rolled_is_not_rolled_again(
    db_session: AsyncSession,
) -> None:
    """The record is the fact Rollover reads, not whether February looks empty."""
    ctx = await _setup(db_session)
    await _expense(db_session, ctx)
    db_session.add_all(
        [
            RolledPeriod(budget_id=ctx.budget_id, period=JAN),
            RolledPeriod(budget_id=ctx.budget_id, period=FEB),
        ]
    )
    await db_session.commit()

    await rollover(db_session, ctx.user, through=FEB)

    assert await _rows(db_session, ctx.budget_id, FEB) == []


@pytest.mark.parametrize("recorded", [True, False], ids=["after-a-roll", "never-rolled"])
async def test_a_late_run_still_rolls_last_month_after_the_user_added_this_months_rows(
    db_session: AsyncSession, recorded: bool
) -> None:
    """The job ran late and the user has already added an Expense in February. Starting
    from the newest Expense would start from February and skip January's walk."""
    ctx = await _setup(db_session)
    source = await _expense(db_session, ctx)
    if recorded:
        db_session.add(RolledPeriod(budget_id=ctx.budget_id, period=JAN))
        await db_session.commit()
    users_own = await _expense(db_session, ctx, name="Haircut", cost="25.00", period=FEB)
    await _pay(db_session, ctx, source, TransactionType.SPEND, "400.00")

    await rollover(db_session, ctx.user, through=FEB)

    rows = await _rows(db_session, ctx.budget_id, FEB)
    assert {e.series_id for e in rows} == {source.series_id, users_own.series_id}
    await db_session.refresh(users_own)
    assert (users_own.name, users_own.cost) == ("Haircut", Decimal("25.00"))


async def test_a_second_rollover_continues_from_the_latest_period_not_the_first(
    db_session: AsyncSession,
) -> None:
    """March is February's plan carried on. The user raised Groceries to £500 in
    February, so a March built from January's rows would bring back the old £400."""
    ctx = await _setup(db_session)
    january = await _expense(db_session, ctx)
    await _pay(db_session, ctx, january, TransactionType.SPEND, "400.00")
    await rollover(db_session, ctx.user, through=FEB)

    (february,) = await _rows(db_session, ctx.budget_id, FEB)
    february.cost = Decimal("500.00")
    await db_session.commit()
    await _pay(db_session, ctx, february, TransactionType.SPEND, "500.00")

    await rollover(db_session, ctx.user, through=MAR)

    (march,) = await _rows(db_session, ctx.budget_id, MAR)
    assert (march.series_id, march.cost) == (january.series_id, Decimal("500.00"))
    assert await _rolled(db_session, ctx.budget_id) == {FEB, MAR}


async def test_a_budget_whose_only_expenses_are_in_the_target_period_has_nothing_to_roll(
    db_session: AsyncSession,
) -> None:
    """A new User's first Expenses, added this month before the job has ever run for
    the Budget. There is no earlier Period to roll from, so nothing happens - copying
    February into February would collide with the rows it was copied from."""
    ctx = await _setup(db_session)
    own = await _expense(db_session, ctx, period=FEB)

    await rollover(db_session, ctx.user, through=FEB)

    rows = await _rows(db_session, ctx.budget_id, FEB)
    assert [e.id for e in rows] == [own.id]


# ---------------------------------------------------------------------------
# Shortfall carries
# ---------------------------------------------------------------------------


async def test_an_unmet_expense_carries_its_shortfall_as_a_once_expense(
    db_session: AsyncSession,
) -> None:
    """A Carried Expense is an ordinary Expense: its own lineage, the source's Category
    and Savings (docs/adr/0018), and a name and note that nothing ever reads."""
    ctx = await _setup(db_session)
    source = await _expense(
        db_session,
        ctx,
        name="Holiday",
        cost="100.00",
        note="summer",
        goal_amount=Decimal("1200.00"),
        goal_date=datetime.date(2026, 12, 31),
    )
    savings_id = await _fund(db_session, ctx, source)
    await _pay(db_session, ctx, source, TransactionType.SAVE, "60.00")

    await rollover(db_session, ctx.user, through=FEB)

    rows = await _rows(db_session, ctx.budget_id, FEB)
    (carry,) = _carries(rows, source)
    assert carry.series_id is not None
    assert {
        "budget_id": carry.budget_id,
        "category_id": carry.category_id,
        "savings_id": carry.savings_id,
        "name": carry.name,
        "note": carry.note,
        "cost": carry.cost,
        "monthly_cost": carry.monthly_cost,
        "frequency": carry.frequency,
        "goal_amount": carry.goal_amount,
        "goal_date": carry.goal_date,
    } == {
        "budget_id": ctx.budget_id,
        "category_id": ctx.category_id,
        "savings_id": savings_id,
        "name": "DEBT Holiday",
        "note": CARRY_NOTE,
        "cost": Decimal("40.00"),
        "monthly_cost": Decimal("40.00"),
        "frequency": Frequency.ONCE,
        "goal_amount": None,
        "goal_date": None,
    }


async def test_the_shortfall_counts_only_undeleted_save_and_spend(
    db_session: AsyncSession,
) -> None:
    """Allocated's rule (docs/adr/0011): Spend Saved was counted when it was Saved, a
    deleted row counts toward nothing, and Income is nobody's Allocation."""
    ctx = await _setup(db_session)
    source = await _expense(db_session, ctx)
    await _fund(db_session, ctx, source)
    await _pay(db_session, ctx, source, TransactionType.SAVE, "50.00")
    await _pay(db_session, ctx, source, TransactionType.SPEND_SAVED, "30.00")
    await _pay(db_session, ctx, source, TransactionType.SPEND, "20.00")
    await _pay(db_session, ctx, source, TransactionType.SPEND, "100.00", deleted=True)
    await _pay(db_session, ctx, None, TransactionType.INCOME, "2000.00")

    await rollover(db_session, ctx.user, through=FEB)

    (carry,) = _carries(await _rows(db_session, ctx.budget_id, FEB), source)
    assert carry.cost == Decimal("330.00")


@pytest.mark.parametrize("paid", ["400.00", "450.00"], ids=["exactly-met", "overpaid"])
async def test_a_met_expense_carries_nothing(db_session: AsyncSession, paid: str) -> None:
    """No Shortfall, and overpaying is never credited to the next Period."""
    ctx = await _setup(db_session)
    source = await _expense(db_session, ctx)
    await _pay(db_session, ctx, source, TransactionType.SPEND, paid)

    await rollover(db_session, ctx.user, through=FEB)

    (successor,) = await _rows(db_session, ctx.budget_id, FEB)
    assert (successor.series_id, successor.cost) == (
        source.series_id,
        Decimal("400.00"),
    )


async def test_an_unmet_once_expense_carries_only_its_shortfall(
    db_session: AsyncSession,
) -> None:
    ctx = await _setup(db_session)
    source = await _expense(db_session, ctx, name="Gift", cost="50.00", frequency=Frequency.ONCE)
    await _pay(db_session, ctx, source, TransactionType.SPEND, "20.00")

    await rollover(db_session, ctx.user, through=FEB)

    rows = await _rows(db_session, ctx.budget_id, FEB)
    assert _shape(rows) == [("DEBT Gift", Frequency.ONCE, Decimal("30.00"))]
    assert rows[0].series_id != source.series_id


async def test_carrying_a_carried_expense_again_does_not_prefix_it_twice(
    db_session: AsyncSession,
) -> None:
    ctx = await _setup(db_session)
    await _expense(
        db_session,
        ctx,
        name="DEBT Groceries",
        cost="100.00",
        frequency=Frequency.ONCE,
        note=CARRY_NOTE,
    )

    await rollover(db_session, ctx.user, through=FEB)

    rows = await _rows(db_session, ctx.budget_id, FEB)
    assert _shape(rows) == [("DEBT Groceries", Frequency.ONCE, Decimal("100.00"))]
    assert rows[0].note == CARRY_NOTE


# ---------------------------------------------------------------------------
# a multi-month gap
# ---------------------------------------------------------------------------

# @TODO
# async def test_a_gap_walks_month_by_month_carrying_each_months_shortfall(
#     db_session: AsyncSession,
# ) -> None:
#     """January was rolled with £300 of £400 spent; the job next runs in April. February
#     and March had no rows, so nothing could be recorded against them (docs/adr/0017)
#     and every Expense in them is unmet. Each step carries every Shortfall, a carry
#     included, as its own row - April ends up owing £1,300 across three carries."""
#     ctx = await _setup(db_session)
#     groceries = await _expense(db_session, ctx)
#     db_session.add(RolledPeriod(budget_id=ctx.budget_id, period=JAN))
#     await db_session.commit()
#     await _pay(db_session, ctx, groceries, TransactionType.SPEND, "300.00")

#     await rollover(db_session, ctx.user, through=APR)

#     feb = await _rows(db_session, ctx.budget_id, FEB)
#     mar = await _rows(db_session, ctx.budget_id, MAR)
#     apr = await _rows(db_session, ctx.budget_id, APR)
#     monthly = ("Groceries", Frequency.MONTHLY, Decimal("400.00"))
#     assert _shape(feb) == [
#         ("DEBT Groceries", Frequency.ONCE, Decimal("100.00")),
#         monthly,
#     ]
#     assert _shape(mar) == [
#         ("DEBT Groceries", Frequency.ONCE, Decimal("100.00")),
#         ("DEBT Groceries", Frequency.ONCE, Decimal("400.00")),
#         monthly,
#     ]
#     assert _shape(apr) == [
#         ("DEBT Groceries", Frequency.ONCE, Decimal("100.00")),
#         ("DEBT Groceries", Frequency.ONCE, Decimal("400.00")),
#         ("DEBT Groceries", Frequency.ONCE, Decimal("400.00")),
#         monthly,
#     ]
#     assert sum(e.cost for e in _carries(apr, groceries)) == Decimal("900.00")

#     # One lineage succeeded throughout; every carry, re-carries included, is a new one.
#     assert all(
#         sum(e.series_id == groceries.series_id for e in rows) == 1
#         for rows in (feb, mar, apr)
#     )
#     carry_lineages = [
#         e.series_id for rows in (feb, mar, apr) for e in _carries(rows, groceries)
#     ]
#     assert len(carry_lineages) == len(set(carry_lineages)) == 6

#     assert await _rolled(db_session, ctx.budget_id) == {JAN, FEB, MAR, APR}
