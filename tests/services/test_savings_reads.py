"""Reading Savings: the Fund split at a Period, the list, and the note.

Against a real Postgres: the split is a grouped aggregate over the derived
`savings_id`, which a mocked session could not compute.
"""

import datetime
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import DeletedRow
from app.models.enums import TransactionType
from app.models.expense import Expense
from app.models.savings import Savings
from app.models.transaction import Transaction
from app.models.user import User
from app.ownership import NotOwned, require_owned
from app.schemas.fields import current_period
from app.schemas.savings import SavingsUpdate
from app.services import savings as savings_service
from tests.services.test_savings import _expense

THIS = current_period()
LAST = (THIS - datetime.timedelta(days=1)).replace(day=1)
NEXT = (THIS + datetime.timedelta(days=32)).replace(day=1)


async def _funded(db: AsyncSession) -> tuple[User, Expense, Savings]:
    user, expense = await _expense(db)
    savings = Savings(user_id=user.id)
    db.add(savings)
    await db.flush()
    expense.savings_id = savings.id
    await db.commit()
    return user, expense, savings


async def _row(
    db: AsyncSession,
    expense: Expense,
    type_: TransactionType,
    amount: str,
    date: datetime.date,
    *,
    deleted: bool = False,
) -> None:
    """A ledger row placed on `date` directly: earlier Periods are a record no route
    can write any more (docs/adr/0017)."""
    db.add(
        Transaction(
            expense_id=expense.id,
            user_id=expense.user_id,
            type=type_,
            name=f"{type_.value} {amount}",
            amount=Decimal(amount),
            date=date,
            deleted_at=func.now() if deleted else None,
        )
    )
    await db.commit()


# ---------------------------------------------------------------------------
# the Fund at a Period
# ---------------------------------------------------------------------------


async def test_the_fund_splits_at_the_start_of_the_period(db_session: AsyncSession) -> None:
    _, expense, savings = await _funded(db_session)
    await _row(db_session, expense, TransactionType.SAVE, "100.00", LAST)
    await _row(db_session, expense, TransactionType.SAVE, "50.00", THIS)
    await _row(db_session, expense, TransactionType.SPEND_SAVED, "20.00", THIS)

    fund = (await savings_service.period_funds(db_session, [savings.id], THIS))[savings.id]

    assert fund.before_period == Decimal("100.00")
    assert fund.period == Decimal("30.00")
    assert fund.total == Decimal("130.00")


async def test_a_past_period_reads_the_fund_as_it_ended(db_session: AsyncSession) -> None:
    """Movements dated after the Period are left out, so the three figures always add
    up - and none of them is today's balance."""
    _, expense, savings = await _funded(db_session)
    await _row(db_session, expense, TransactionType.SAVE, "100.00", LAST)
    await _row(db_session, expense, TransactionType.SAVE, "50.00", THIS)

    fund = (await savings_service.period_funds(db_session, [savings.id], LAST))[savings.id]

    assert (fund.before_period, fund.period, fund.total) == (
        Decimal(0),
        Decimal("100.00"),
        Decimal("100.00"),
    )


async def test_placement_is_by_the_transactions_date(db_session: AsyncSession) -> None:
    """Not the Expense's Period: the row here sits on this Period's Expense, dated last
    month, and counts as before the Period."""
    _, expense, savings = await _funded(db_session)
    await _row(db_session, expense, TransactionType.SAVE, "40.00", LAST)

    fund = (await savings_service.period_funds(db_session, [savings.id], THIS))[savings.id]

    assert (fund.before_period, fund.period) == (Decimal("40.00"), Decimal(0))


async def test_deleted_movements_count_toward_nothing(db_session: AsyncSession) -> None:
    _, expense, savings = await _funded(db_session)
    await _row(db_session, expense, TransactionType.SAVE, "100.00", THIS)
    await _row(db_session, expense, TransactionType.SAVE, "999.00", THIS, deleted=True)

    fund = (await savings_service.period_funds(db_session, [savings.id], THIS))[savings.id]

    assert fund.total == Decimal("100.00")


async def test_the_current_period_total_is_the_balance(db_session: AsyncSession) -> None:
    """The figure the spend split draws on and the figure the route shows agree."""
    _, expense, savings = await _funded(db_session)
    await _row(db_session, expense, TransactionType.SAVE, "100.00", LAST)
    await _row(db_session, expense, TransactionType.TRANSFER, "30.00", THIS)

    fund = (await savings_service.period_funds(db_session, [savings.id], THIS))[savings.id]

    assert fund.total == await savings_service.balance(db_session, savings.id)
    assert fund.total == Decimal("70.00")


async def test_a_fund_with_no_movements_reads_zero(db_session: AsyncSession) -> None:
    _, _, savings = await _funded(db_session)

    fund = (await savings_service.period_funds(db_session, [savings.id], THIS))[savings.id]

    assert (fund.before_period, fund.period) == (Decimal(0), Decimal(0))


async def test_many_funds_in_one_call_stay_apart(db_session: AsyncSession) -> None:
    _, a, a_savings = await _funded(db_session)
    _, b, b_savings = await _funded(db_session)
    await _row(db_session, a, TransactionType.SAVE, "10.00", THIS)
    await _row(db_session, b, TransactionType.SAVE, "20.00", THIS)

    funds = await savings_service.period_funds(
        db_session, [a_savings.id, b_savings.id], THIS
    )

    assert funds[a_savings.id].total == Decimal("10.00")
    assert funds[b_savings.id].total == Decimal("20.00")


# ---------------------------------------------------------------------------
# the list
# ---------------------------------------------------------------------------


async def test_the_list_is_the_callers_in_the_order_they_were_opened(
    db_session: AsyncSession,
) -> None:
    user, _, first = await _funded(db_session)
    second = Savings(user_id=user.id)
    db_session.add(second)
    await db_session.commit()
    await _funded(db_session)  # someone else's

    listed = await savings_service.list_savings(db_session, user, include_deleted=False)

    assert [s.id for s in listed] == [first.id, second.id]


async def test_a_closed_savings_is_listed_only_when_asked_for(
    db_session: AsyncSession,
) -> None:
    user, _, savings = await _funded(db_session)
    savings.deleted_at = func.now()
    await db_session.commit()

    hidden = await savings_service.list_savings(db_session, user, include_deleted=False)
    shown = await savings_service.list_savings(db_session, user, include_deleted=True)

    assert hidden == []
    assert [s.id for s in shown] == [savings.id]


# ---------------------------------------------------------------------------
# lookup and the note
# ---------------------------------------------------------------------------


async def test_the_note_is_editable(db_session: AsyncSession) -> None:
    _, _, savings = await _funded(db_session)

    updated = await savings_service.update_savings(
        db_session, savings, SavingsUpdate(note="Japan, spring")
    )

    assert updated.note == "Japan, spring"


async def test_a_closed_savings_is_readable_but_not_writable(
    db_session: AsyncSession,
) -> None:
    user, _, savings = await _funded(db_session)
    savings.deleted_at = func.now()
    await db_session.commit()

    read = await require_owned(db_session, Savings, savings.id, user, allow_deleted=True)
    assert read.id == savings.id
    with pytest.raises(DeletedRow):
        await require_owned(db_session, Savings, savings.id, user)


async def test_another_users_savings_is_not_found(db_session: AsyncSession) -> None:
    _, _, savings = await _funded(db_session)
    stranger, _ = await _expense(db_session)

    with pytest.raises(NotOwned):
        await require_owned(
            db_session, Savings, savings.id, stranger, allow_deleted=True
        )


async def test_an_unknown_id_is_not_found(db_session: AsyncSession) -> None:
    user, _, _ = await _funded(db_session)

    with pytest.raises(NotOwned):
        await require_owned(db_session, Savings, uuid.uuid4(), user, allow_deleted=True)
