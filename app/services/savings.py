"""Savings funds, and the balances derived from the ledger.

A fund stores no balance. It is the sum of the Transactions whose `savings_id` names
it, which is what stops the number drifting the way `Expense.amount_saved` did - there
is no second copy to forget to update in a write path.

    fund(S) = SAVE - SPEND_SAVED - TRANSFER

`TRANSFER` appears once, not twice: only the fund side of a transfer pair has an Expense,
so the Pool side lands in the Pool sum instead. `INCOME` and `SPEND` never touch a fund.

`Transaction.savings_id` is derived through the Transaction's Expense (docs/adr/0015),
so a fund is reached through whichever Expense rows point at it - every Period of the
lineage, since Rollover carries the id forward.

Performance is not a reason to cache this. A decade of heavy use is tens of thousands of
Transactions for an entire account. What derivation genuinely costs is the inability to
write "a fund never goes negative" as a CHECK - the invariant spans rows - which is why
`spendable` exists and why the split reads it inside the write transaction rather than
trusting a column.

See docs/adr/0011.
"""

import datetime
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import ColumnElement, Subquery, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.enums import TransactionType
from app.models.expense import Expense
from app.models.savings import Savings
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.fields import last_of_month
from app.schemas.savings import SavingsUpdate
from app.services.visibility import live_savings, live_transactions


async def balance(db: AsyncSession, savings_id: uuid.UUID) -> Decimal:
    visible = live_transactions().subquery()
    query = (
        select(func.coalesce(func.sum(_delta(visible)), Decimal(0)))
        .select_from(visible)
        .where(
            visible.c.user_id == _owner(savings_id),
            visible.c.savings_id == savings_id,
        )
    )
    return Decimal(await db.scalar(query) or 0)


def _owner(savings_id: uuid.UUID) -> ColumnElement[uuid.UUID]:
    return select(Savings.user_id).where(Savings.id == savings_id).scalar_subquery()


def _delta(visible: Subquery) -> ColumnElement[Decimal]:
    """Signed contribution of each row to its fund.

    One CASE so a balance is a single aggregate rather than three sums subtracted in
    Python; `INCOME` and `SPEND` fall through to zero because neither touches a fund.
    """
    return case(
        (visible.c.type == TransactionType.SAVE, visible.c.amount),
        (visible.c.type == TransactionType.SPEND_SAVED, -visible.c.amount),
        (visible.c.type == TransactionType.TRANSFER, -visible.c.amount),
        else_=Decimal(0),
    )


@dataclass(frozen=True)
class PeriodFund:
    before_period: Decimal
    period: Decimal

    @property
    def total(self) -> Decimal:
        return self.before_period + self.period


async def period_funds(
    db: AsyncSession, savings_ids: Iterable[uuid.UUID], period: datetime.date
) -> dict[uuid.UUID, PeriodFund]:
    ids = set(savings_ids)
    funds = dict.fromkeys(ids, PeriodFund(Decimal(0), Decimal(0)))
    if not ids:
        return funds
    visible = live_transactions().subquery()
    delta = _delta(visible)
    rows = await db.execute(
        select(
            visible.c.savings_id,
            func.sum(case((visible.c.date < period, delta), else_=Decimal(0))),
            func.sum(case((visible.c.date >= period, delta), else_=Decimal(0))),
        )
        .where(
            # Same reason as `_owner`: without it the scan covers every User.
            visible.c.user_id.in_(select(Savings.user_id).where(Savings.id.in_(ids))),
            visible.c.savings_id.in_(ids),
            visible.c.date <= last_of_month(period),
        )
        .group_by(visible.c.savings_id)
    )
    for savings_id, before_period, within in rows.tuples():
        funds[savings_id] = PeriodFund(before_period, within)
    return funds


async def list_savings(
    db: AsyncSession, user: User, *, include_deleted: bool
) -> Sequence[Savings]:
    query = (
        live_savings(include_deleted=include_deleted)
        .where(Savings.user_id == user.id)
        .order_by(Savings.created_at, Savings.id)
    )
    return (await db.scalars(query)).all()


async def update_savings(
    db: AsyncSession, savings: Savings, data: SavingsUpdate
) -> Savings:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(savings, field, value)
    await db.commit()
    await db.refresh(savings)
    return savings


class FundOverdrawn(AppError):
    def __init__(self, shortfall: Decimal, reason: str, *, status_code: int) -> None:
        super().__init__(
            f"this would leave a savings fund {shortfall} below zero: {reason}",
            status_code=status_code,
        )


async def balances(
    db: AsyncSession, savings_ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, Decimal]:
    """Each fund's balance, taken before a change so `shortfall` can compare."""
    return {sid: await balance(db, sid) for sid in set(savings_ids) if sid is not None}


async def shortfall(
    db: AsyncSession, before: dict[uuid.UUID, Decimal]
) -> Decimal | None:
    worst: Decimal | None = None
    for savings_id, was in before.items():
        now = await balance(db, savings_id)
        if now < 0 and now < was and (worst is None or -now > worst):
            worst = -now
    return worst


async def spendable(db: AsyncSession, expense: Expense) -> Decimal:
    """How much of `expense`'s fund a spend may draw on, never below zero.

    An Expense with no `savings_id` has no fund, which is the common case and the reason
    the column is nullable - it needs no special-casing at the call site.
    """
    if expense.savings_id is None:
        return Decimal(0)
    return max(await balance(db, expense.savings_id), Decimal(0))


async def open_savings(db: AsyncSession, expense: Expense, user: User) -> Savings:
    """The fund for `expense`'s lineage, created on first use.

    Called from the `SAVE` path only: a fund exists because money went into it, never
    because an Expense was created. The id is stamped on the Expense row that triggered
    it and carried forward from there by Rollover; earlier Periods keep their null,
    which is accurate - they had no fund.
    """
    if expense.savings_id is not None:
        existing = await db.get(Savings, expense.savings_id)
        if existing is not None and existing.deleted_at is None:
            return existing

    savings = Savings(user_id=user.id)
    db.add(savings)
    await db.flush()
    expense.savings_id = savings.id
    return savings


async def close_savings(db: AsyncSession, expense: Expense) -> None:
    """End the fund, returning whatever it holds to the Pool.

    Called when the current-Period Expense of a lineage is deleted. The money is real
    and predates the decision to stop planning for it, so it goes back to the Pool as a
    `TRANSFER` pair rather than evaporating:

        row 1  expense_id set, transfer_id -> row 1  -> the fund side, reduces the balance
        row 2  no expense,     transfer_id -> row 1  -> the Pool side, increases the Pool

    The same shape as a Transfer a User asks for (docs/adr/0016), so the pair is one
    group. Direction is readable off `expense_id`, so `transfer_id` carries no meaning
    beyond grouping the two. Both rows are written here rather than by a client, because a
    half-written pair is money that exists on one side of a transfer and not the other -
    a state no read path could detect.

    Not an `INCOME` row, which is the tempting shortcut and would be wrong: income
    asserts money arrived from outside, and nothing arrived. It would inflate the
    account above the bank by the amount of the fund.

    Idempotent by way of the `deleted_at` check - closing an already-closed fund is a
    no-op rather than a second drain.
    """
    if expense.savings_id is None:
        return

    savings = await db.get(Savings, expense.savings_id)
    if savings is None or savings.deleted_at is not None:
        return

    held = await balance(db, savings.id)
    if held > Decimal(0):
        # UTC, matching `current_period()` - the alternative reads the local clock where
        # the rest of the app reads UTC, and moves the day near a month boundary.
        today = datetime.datetime.now(datetime.UTC).date()
        out_of_fund = Transaction(
            expense_id=expense.id,
            user_id=expense.user_id,
            type=TransactionType.TRANSFER,
            name=f"Closed savings for {expense.name}",
            amount=held,
            date=today,
        )
        db.add(out_of_fund)
        # Flushed for its id, which the database generates: both rows point at it.
        await db.flush()
        out_of_fund.transfer_id = out_of_fund.id
        db.add(
            Transaction(
                expense_id=None,
                user_id=expense.user_id,
                type=TransactionType.TRANSFER,
                name=f"Returned from {expense.name} savings",
                amount=held,
                date=today,
                transfer_id=out_of_fund.id,
            )
        )

    savings.deleted_at = func.now()


async def restore_savings(db: AsyncSession, expense: Expense) -> None:
    """Restores savings that were closed from the close_savings"""
    savings = await db.get(Savings, expense.savings_id)
    if savings is None or savings.deleted_at != expense.deleted_at:
        return
    savings.deleted_at = None
