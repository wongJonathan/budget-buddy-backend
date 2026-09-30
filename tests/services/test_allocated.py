"""Allocated: how much of an Expense's Monthly Cost its Period has drawn from the Pool.

Against a real Postgres, because the figure is a sum over the ledger and every case here
is about which rows the server wrote - a split, a Transfer group, a withdrawal - none of
which a mocked session produces.

The rule is Met's (docs/adr/0011): undeleted `SAVE` and `SPEND` count, `SPEND_SAVED` and
`TRANSFER` do not.
"""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import TransactionType
from app.services import expense as expense_service
from app.services import transaction as transaction_service
from tests.services.test_savings import _expense, _fund_from_last_period, _post
from tests.services.test_transfer import _delete_expense, _funded, _transfer


async def test_an_expense_with_no_transactions_reads_zero(db_session: AsyncSession) -> None:
    """Every id asked for gets an entry, so a new Expense is not a KeyError."""
    _, expense = await _expense(db_session)

    assert await expense_service.allocated(db_session, [expense.id]) == {expense.id: Decimal(0)}


async def test_no_ids_is_no_query(db_session: AsyncSession) -> None:
    assert await expense_service.allocated(db_session, []) == {}


async def test_spend_and_save_both_count(db_session: AsyncSession) -> None:
    """Allocated, not spent: money set aside counts as much as money paid out."""
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SPEND, "30.00")
    await _post(db_session, user, expense, TransactionType.SAVE, "50.00")

    totals = await expense_service.allocated(db_session, [expense.id])

    assert totals[expense.id] == Decimal("80.00")


async def test_a_split_spend_counts_only_its_pool_half(db_session: AsyncSession) -> None:
    """£50 with £30 in the fund is £30 Spend Saved plus £20 Spend, and only the £20
    was drawn from the Pool this Period - the £30 was Allocated when it was Saved."""
    user, expense = await _expense(db_session)
    await _fund_from_last_period(db_session, user, expense, "30.00")

    await _post(db_session, user, expense, TransactionType.SPEND, "50.00")

    totals = await expense_service.allocated(db_session, [expense.id])
    assert totals[expense.id] == Decimal("20.00")


async def test_income_and_other_expenses_do_not_leak_in(db_session: AsyncSession) -> None:
    user, a = await _expense(db_session)
    _, b = await _expense(db_session)
    await _post(db_session, user, None, TransactionType.INCOME, "1000.00")
    await _post(db_session, user, a, TransactionType.SPEND, "10.00")

    totals = await expense_service.allocated(db_session, [a.id, b.id])

    assert totals == {a.id: Decimal("10.00"), b.id: Decimal(0)}


async def test_a_transfer_out_leaves_the_source_unchanged(db_session: AsyncSession) -> None:
    """Transfer is Met-neutral: moving money out of a fund does not undo the
    Allocation that put it there."""
    user, a, b = await _funded(db_session)
    await _post(db_session, user, a, TransactionType.SAVE, "25.00")

    await _transfer(db_session, user, a, None, "40.00")

    totals = await expense_service.allocated(db_session, [a.id])
    assert totals[a.id] == Decimal("25.00")


async def test_a_transfer_into_a_fund_counts_for_the_destination(
    db_session: AsyncSession,
) -> None:
    """Fund to fund is a Transfer to the Pool and a Save out of it, and the Save
    counts by the ordinary rule (docs/adr/0016)."""
    user, a, b = await _funded(db_session)

    await _transfer(db_session, user, a, b, "40.00")

    totals = await expense_service.allocated(db_session, [a.id, b.id])
    assert totals == {a.id: Decimal(0), b.id: Decimal("40.00")}


async def test_a_deleted_transaction_stops_counting(db_session: AsyncSession) -> None:
    user, expense = await _expense(db_session)
    [kept] = await _post(db_session, user, expense, TransactionType.SPEND, "10.00")
    [gone] = await _post(db_session, user, expense, TransactionType.SPEND, "90.00")

    await transaction_service.soft_delete_transaction(db_session, gone)

    totals = await expense_service.allocated(db_session, [expense.id])
    assert totals[expense.id] == Decimal("10.00")


async def test_a_deleted_expense_reads_zero_and_restore_brings_it_back(
    db_session: AsyncSession,
) -> None:
    """Deleting an Expense withdraws its Period's Transactions, so nothing on it counts;
    Restore returns exactly those rows, and the figure with them."""
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SPEND, "60.00")
    await _post(db_session, user, expense, TransactionType.SAVE, "15.00")

    await _delete_expense(db_session, expense)
    assert (await expense_service.allocated(db_session, [expense.id]))[expense.id] == 0

    await expense_service.restore_expense(db_session, expense)
    totals = await expense_service.allocated(db_session, [expense.id])
    assert totals[expense.id] == Decimal("75.00")
