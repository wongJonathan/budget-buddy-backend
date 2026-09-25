"""Restoring a deleted Expense, the exact inverse of the delete (docs/adr/0014).

Against a real Postgres. Every match here is a timestamp equality - `deleted_at` for the
withdrawn rows, `created_at` for the drain pair - and those values only mean something
when Postgres's `now()` produced them, fixed per DB transaction.

The drain pair is the part that is easy to get subtly wrong. The Pool side carries no
`expense_id` or `savings_id`, so a lookup keyed on those misses it and leaves the money
counted twice; a lookup keyed on the lineage alone catches pairs from earlier deletes.
Most of this file is those scenarios.
"""

import datetime
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import CannotRestoreNonDeletedRow
from app.models.enums import TransactionType
from app.models.expense import Expense
from app.models.savings import Savings
from app.models.transaction import Transaction
from app.models.user import User
from app.services import expense as expense_service
from app.services import savings as savings_service
from app.services.expense import ClosedPeriod
from tests.services.test_savings import (
    _expense,
    _fund_from_last_period,
    _live_pool,
    _post,
    _transactions,
)


async def _delete(db: AsyncSession, expense: Expense) -> None:
    await expense_service.soft_delete_expense(db, expense)
    # `deleted_at = func.now()` leaves the attribute expired after the flush, and the
    # restore reads it - reload it here the way the route's path lookup would.
    await db.refresh(expense)


async def _restore(db: AsyncSession, expense: Expense) -> Expense:
    return await expense_service.restore_expense(db, expense)


def _transfers(rows: list[Transaction], *, live: bool | None = None) -> list[Transaction]:
    return [
        t
        for t in rows
        if t.type is TransactionType.TRANSFER and (live is None or (t.deleted_at is None) is live)
    ]


async def _total(db: AsyncSession, user: User, expense: Expense) -> tuple[Decimal, Decimal]:
    """(Pool, fund) - the two numbers a restore has to put back."""
    assert expense.savings_id is not None
    return (
        _live_pool(await _transactions(db, user)),
        await savings_service.balance(db, expense.savings_id),
    )


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------


async def test_restoring_a_live_expense_is_refused(db_session: AsyncSession) -> None:
    _, expense = await _expense(db_session)

    with pytest.raises(CannotRestoreNonDeletedRow):
        await _restore(db_session, expense)


async def test_restoring_an_expense_from_a_closed_period_is_refused(
    db_session: AsyncSession,
) -> None:
    """Deletion is only allowed in the open Period, so this is "deleted this Period"."""
    _, expense = await _expense(db_session)
    expense.period = (expense.period - datetime.timedelta(days=1)).replace(day=1)
    expense.deleted_at = func.now()
    await db_session.commit()
    await db_session.refresh(expense)

    with pytest.raises(ClosedPeriod):
        await _restore(db_session, expense)


# ---------------------------------------------------------------------------
# the Expense and its withdrawn Transactions
# ---------------------------------------------------------------------------


async def test_restore_undeletes_the_expense(db_session: AsyncSession) -> None:
    _, expense = await _expense(db_session)
    await _delete(db_session, expense)

    restored = await _restore(db_session, expense)

    assert restored.deleted_at is None


async def test_restore_brings_back_the_transactions_the_delete_withdrew(
    db_session: AsyncSession,
) -> None:
    """SAVE, SPEND_SAVED and SPEND all come back - everything carrying this
    `expense_id` that the delete's DB transaction soft-deleted."""
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SAVE, "30.00")
    await _post(db_session, user, expense, TransactionType.SPEND, "50.00")
    await _delete(db_session, expense)

    await _restore(db_session, expense)

    rows = await _transactions(db_session, user)
    assert {(t.type, t.amount, t.deleted_at) for t in rows} == {
        (TransactionType.SAVE, Decimal("30.00"), None),
        (TransactionType.SPEND_SAVED, Decimal("30.00"), None),
        (TransactionType.SPEND, Decimal("20.00"), None),
    }


async def test_a_transaction_the_user_deleted_earlier_stays_deleted(
    db_session: AsyncSession,
) -> None:
    """Its `deleted_at` is from an earlier DB transaction, so it doesn't match."""
    user, expense = await _expense(db_session)
    (kept,) = await _post(db_session, user, expense, TransactionType.SPEND, "10.00")
    (removed,) = await _post(db_session, user, expense, TransactionType.SPEND, "20.00")
    removed.deleted_at = func.now()
    await db_session.commit()
    await _delete(db_session, expense)

    await _restore(db_session, expense)

    await db_session.refresh(kept)
    await db_session.refresh(removed)
    assert kept.deleted_at is None
    assert removed.deleted_at is not None


async def test_an_expense_with_no_fund_restores(db_session: AsyncSession) -> None:
    """The common case: nothing to reopen, no pair to look for."""
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SPEND, "20.00")
    await _delete(db_session, expense)

    await _restore(db_session, expense)

    rows = await _transactions(db_session, user)
    assert [(t.type, t.deleted_at) for t in rows] == [(TransactionType.SPEND, None)]


# ---------------------------------------------------------------------------
# the fund
# ---------------------------------------------------------------------------


async def test_restore_reopens_the_fund(db_session: AsyncSession) -> None:
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SAVE, "50.00")
    await _delete(db_session, expense)

    await _restore(db_session, expense)

    savings = await db_session.get(Savings, expense.savings_id)
    assert savings is not None and savings.deleted_at is None


async def test_a_fund_closed_by_something_else_stays_closed(db_session: AsyncSession) -> None:
    """Only a fund *this* delete closed is reopened - matched by equal `deleted_at`.
    `close_savings` is a no-op on an already-closed fund, so the delete leaves the
    earlier timestamp in place."""
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SAVE, "50.00")
    savings = await db_session.get(Savings, expense.savings_id)
    assert savings is not None
    savings.deleted_at = func.now()
    await db_session.commit()
    # Expired by the `func.now()` write, and `close_savings` reads it back through the
    # identity map - reload it, as a fresh request's session would.
    await db_session.refresh(savings)
    await _delete(db_session, expense)

    await _restore(db_session, expense)

    await db_session.refresh(savings)
    assert savings.deleted_at is not None


async def test_an_empty_fund_restores_without_a_pair(db_session: AsyncSession) -> None:
    """The fund was empty at delete time, so the delete wrote no pair and there is
    nothing to withdraw - but the fund still reopens."""
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SAVE, "50.00")
    await _post(db_session, user, expense, TransactionType.SPEND, "50.00")
    await _delete(db_session, expense)

    await _restore(db_session, expense)

    rows = await _transactions(db_session, user)
    assert not _transfers(rows)
    savings = await db_session.get(Savings, expense.savings_id)
    assert savings is not None and savings.deleted_at is None


# ---------------------------------------------------------------------------
# the drain pair
# ---------------------------------------------------------------------------


async def test_restore_withdraws_both_sides_of_the_drain(db_session: AsyncSession) -> None:
    """The Pool side has no `expense_id` or `savings_id`. A lookup keyed on those voids
    only the fund side: the fund regains the money and the Pool keeps it too."""
    user, expense = await _expense(db_session)
    await _fund_from_last_period(db_session, user, expense, "100.00")
    await _delete(db_session, expense)

    await _restore(db_session, expense)

    transfers = _transfers(await _transactions(db_session, user))
    assert len(transfers) == 2
    assert all(t.deleted_at is not None for t in transfers)


async def test_restore_puts_the_pool_and_fund_back(db_session: AsyncSession) -> None:
    """The observable consequence of the test above, and the one that matters: the
    delete-restore round trip leaves both numbers exactly where they started."""
    user, expense = await _expense(db_session)
    await _post(db_session, user, None, TransactionType.INCOME, "500.00")
    await _fund_from_last_period(db_session, user, expense, "100.00")
    await _post(db_session, user, expense, TransactionType.SAVE, "30.00")
    before = await _total(db_session, user, expense)

    await _delete(db_session, expense)
    assert await _total(db_session, user, expense) != before
    await _restore(db_session, expense)

    assert (
        await _total(db_session, user, expense)
        == before
        == (
            Decimal("370.00"),
            Decimal("130.00"),
        )
    )


async def test_delete_restore_twice_voids_each_pair_once(db_session: AsyncSession) -> None:
    """Each delete writes a new pair against the same `expense_id` and `savings_id`. The
    second restore must void the second pair, and the first pair - already voided by
    the first restore - must keep the timestamp it was voided with."""
    user, expense = await _expense(db_session)
    await _post(db_session, user, None, TransactionType.INCOME, "500.00")
    await _fund_from_last_period(db_session, user, expense, "100.00")
    before = await _total(db_session, user, expense)

    await _delete(db_session, expense)
    await _restore(db_session, expense)
    first_pair = _transfers(await _transactions(db_session, user))
    first_voided_at = {t.id: t.deleted_at for t in first_pair}

    await _delete(db_session, expense)
    rows = await _transactions(db_session, user)
    live = _transfers(rows, live=True)
    assert len(live) == 2, "the second delete should add exactly one live pair"
    assert not {t.id for t in live} & first_voided_at.keys()

    await _restore(db_session, expense)

    rows = await _transactions(db_session, user)
    assert len(_transfers(rows)) == 4
    assert not _transfers(rows, live=True)
    assert {t.id: t.deleted_at for t in rows if t.id in first_voided_at} == first_voided_at
    assert await _total(db_session, user, expense) == before


async def test_a_second_delete_after_restore_drains_the_full_amount_again(
    db_session: AsyncSession,
) -> None:
    """If restore left the first pair's fund side live, the fund would read zero after
    restore and the second delete would drain nothing."""
    user, expense = await _expense(db_session)
    await _fund_from_last_period(db_session, user, expense, "100.00")

    await _delete(db_session, expense)
    await _restore(db_session, expense)
    await _delete(db_session, expense)

    live = _transfers(await _transactions(db_session, user), live=True)
    assert {t.amount for t in live} == {Decimal("100.00")}


async def test_another_expenses_drain_is_left_alone(db_session: AsyncSession) -> None:
    """Two funded Expenses deleted separately: restoring one must not void the other's
    pair, even though both are this user's live TRANSFERs."""
    user, groceries = await _expense(db_session)
    await _fund_from_last_period(db_session, user, groceries, "100.00")
    rent = Expense(
        budget_id=groceries.budget_id,
        category_id=groceries.category_id,
        user_id=user.id,
        name="Rent",
        cost=Decimal("900.00"),
        frequency=groceries.frequency,
        period=groceries.period,
    )
    db_session.add(rent)
    await db_session.commit()
    await _fund_from_last_period(db_session, user, rent, "40.00")

    await _delete(db_session, groceries)
    await _delete(db_session, rent)
    await _restore(db_session, groceries)

    live = _transfers(await _transactions(db_session, user), live=True)
    assert len(live) == 2
    assert {t.amount for t in live} == {Decimal("40.00")}


async def test_rows_sharing_the_timestamp_but_not_the_user_or_type_are_left_alone(
    db_session: AsyncSession,
) -> None:
    """The drain is found by `created_at == expense.deleted_at`. Another DB transaction
    starting in the same microsecond would share that `now()` - forged here by writing
    `created_at` directly. Scoping on `user_id` and `type` is what keeps a restore from
    voiding another user's transfer, or this user's non-transfer row."""
    user, expense = await _expense(db_session)
    await _fund_from_last_period(db_session, user, expense, "100.00")
    await _delete(db_session, expense)
    assert expense.deleted_at is not None

    other = User(
        display_name="Bob",
        email=f"bob-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
    )
    db_session.add(other)
    await db_session.flush()
    bystanders = [
        Transaction(
            user_id=other.id,
            type=TransactionType.TRANSFER,
            name="Bob's transfer",
            amount=Decimal("5.00"),
            date=datetime.date.today(),
            created_at=expense.deleted_at,
        ),
        Transaction(
            user_id=user.id,
            type=TransactionType.INCOME,
            name="Same-instant income",
            amount=Decimal("5.00"),
            date=datetime.date.today(),
            created_at=expense.deleted_at,
        ),
    ]
    db_session.add_all(bystanders)
    await db_session.commit()

    await _restore(db_session, expense)

    for row in bystanders:
        await db_session.refresh(row)
        assert row.deleted_at is None, row.name
