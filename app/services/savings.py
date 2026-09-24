"""Savings funds, and the balances derived from the ledger.

A fund stores no balance. It is the sum of the Transactions stamped with its
`savings_id`, which is what stops the number drifting the way `Expense.amount_saved`
did - there is no second copy to forget to update in a write path.

    fund(S) = SAVE - SPEND_SAVED - TRANSFER

`TRANSFER` appears once, not twice: only the fund side of a transfer pair is stamped, so
the Pool side lands in the Pool sum instead. `INCOME` and `SPEND` never touch a fund and
are never stamped.

The sum reads `Transaction.savings_id` rather than joining out to `expenses.savings_id`:
those answer different questions - which fund the money went into, versus which fund the
lineage feeds now - and only the first is a fact about the past. See the note on the
Transaction model.

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
from app.services.visibility import live_transactions


async def balance(db: AsyncSession, savings_id: uuid.UUID) -> Decimal:
    """What the savings fund holds right now.

    Built on `live_transactions`, like every other read. That was briefly impossible:
    while Transaction was subject to the ancestor rule, a soft-deleted Expense hid its
    own SAVE rows and the fund quietly read zero, so this had to filter the deleted flag
    for itself. ADR-0007 as amended took Transaction out of that rule and the special
    case went with it - a fund ends through its own `deleted_at`, and a movement stops
    counting through the Transaction's.
    """
    visible = live_transactions().subquery()
    # Signed contribution of each row. One CASE so the balance is a single aggregate
    # rather than three sums subtracted in Python; `INCOME` and `SPEND` fall through to
    # zero because neither touches a fund.
    delta = case(
        (visible.c.type == TransactionType.SAVE, visible.c.amount),
        (visible.c.type == TransactionType.SPEND_SAVED, -visible.c.amount),
        (visible.c.type == TransactionType.TRANSFER, -visible.c.amount),
        else_=Decimal(0),
    )
    query = (
        select(func.coalesce(func.sum(delta), Decimal(0)))
        .select_from(visible)
        .where(visible.c.savings_id == savings_id)
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

        row 1  expense_id + savings_id set   -> the fund side, reduces the balance
        row 2  both null, transfer_id -> row 1 -> the Pool side, increases the Pool

    Direction is readable off those columns, so `transfer_id` carries no meaning beyond
    pairing the two. Both rows are written here rather than by a client, because a
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

    savings.deleted_at = func.now()
