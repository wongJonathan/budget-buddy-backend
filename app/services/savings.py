"""Savings funds, and the balances derived from the ledger.

A fund stores no balance. It is the sum of the Transactions on the Expense lineage
pointing at it, which is what stops the number drifting the way `Expense.amount_saved`
did - there is no second copy to forget to update in a write path.

    fund(S) = SAVE - SPEND_SAVED - TRANSFER

`TRANSFER` appears once, not twice: a transfer pair is one row carrying the source
`expense_id` and one with a null `expense_id` for the Pool side. Only the first joins to
an Expense, so only the fund side is counted here, and the Pool side lands in the Pool
sum instead. `INCOME` and `SPEND` never touch a fund.

Performance is not a reason to cache this. A decade of heavy use is tens of thousands of
Transactions for an entire account, and the sum below is filtered by an indexed
`savings_id`. What derivation genuinely costs is the inability to write "a fund never
goes negative" as a CHECK - the invariant spans rows - which is why `spendable` exists
and why the split reads it inside the write transaction rather than trusting a column.

See docs/adr/0011.
"""

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import TransactionType
from app.models.expense import Expense
from app.models.savings import Savings
from app.models.transaction import Transaction
from app.models.user import User

# Signed contribution of one Transaction to the fund it is stamped with. One CASE so a
# balance is a single aggregate, rather than three sums subtracted in Python. `INCOME`
# and `SPEND` fall through to zero: neither touches a fund.
_FUND_DELTA = case(
    (Transaction.type == TransactionType.SAVE, Transaction.amount),
    (Transaction.type == TransactionType.SPEND_SAVED, -Transaction.amount),
    (Transaction.type == TransactionType.TRANSFER, -Transaction.amount),
    else_=Decimal(0),
)


async def balance(db: AsyncSession, savings_id: uuid.UUID) -> Decimal:
    """What the savings fund holds right now.

    Deliberately *not* built on `live_transactions`, which makes this the one read in
    the codebase that departs from it. That select applies the ancestor rule - a
    Transaction is hidden when its Expense or Budget is soft-deleted - which is right
    for listing what a user can see and wrong for counting money. A fund whose lineage
    was deleted still holds real money, and reporting zero would be a silent revaluation
    rather than a hidden row. A fund ends through its own `is_deleted`; a movement stops
    counting through the Transaction's.
    """
    query = select(func.coalesce(func.sum(_FUND_DELTA), Decimal(0))).where(
        Transaction.savings_id == savings_id,
        ~Transaction.is_deleted,
    )
    return Decimal(await db.scalar(query) or 0)


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
        if existing is not None and not existing.is_deleted:
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

        row 1  expense_id + savings_id set   -> the fund side, reduces the balance
        row 2  both null, transfer_id -> row 1 -> the Pool side, increases the Pool

    Direction is readable off those columns, so `transfer_id` carries no meaning beyond
    pairing the two. Both rows are written here rather than by a client, because a
    half-written pair is money that exists on one side of a transfer and not the other -
    a state no read path could detect.

    Not an `INCOME` row, which is the tempting shortcut and would be wrong: income
    asserts money arrived from outside, and nothing arrived. It would inflate the
    account above the bank by the amount of the fund.

    Idempotent by way of the `is_deleted` check - closing an already-closed fund is a
    no-op rather than a second drain.
    """
    if expense.savings_id is None:
        return

    savings = await db.get(Savings, expense.savings_id)
    if savings is None or savings.is_deleted:
        return

    held = await balance(db, savings.id)
    if held > Decimal(0):
        # UTC, matching `current_period()` - the alternative reads the local clock where
        # the rest of the app reads UTC, and moves the day near a month boundary.
        today = datetime.datetime.now(datetime.UTC).date()
        out_of_fund = Transaction(
            expense_id=expense.id,
            savings_id=savings.id,
            user_id=expense.user_id,
            type=TransactionType.TRANSFER,
            name=f"Closed savings for {expense.name}",
            amount=held,
            date=today,
        )
        db.add(out_of_fund)
        # Flushed so the Pool side has an id to point at: one row must exist before the
        # other can reference it, which is what makes the pair's order inherent.
        await db.flush()
        db.add(
            Transaction(
                expense_id=None,
                savings_id=None,
                user_id=expense.user_id,
                type=TransactionType.TRANSFER,
                name=f"Returned from {expense.name} savings",
                amount=held,
                date=today,
                transfer_id=out_of_fund.id,
            )
        )

    savings.is_deleted = True
