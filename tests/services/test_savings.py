"""Derived fund balances, and the fund-first spend split.

Against a real Postgres. The balance is a SQL aggregate over a join, and the split reads
it inside the write path - a mocked session hands back whatever a test stubbed, so it
could not tell a correct sum from a wrong one.

See docs/adr/0011.
"""

import datetime
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget import Budget
from app.models.category import Category
from app.models.enums import Frequency, TransactionType
from app.models.expense import Expense
from app.models.savings import Savings
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.fields import current_period
from app.schemas.transaction import TransactionCreate
from app.services import expense as expense_service
from app.services import savings as savings_service
from app.services import transaction as transaction_service


async def _expense(db: AsyncSession, *, cost: str = "400.00") -> tuple[User, Expense]:
    """A User with one Expense and no fund."""
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

    expense = Expense(
        budget_id=budget.id,
        category_id=category.id,
        user_id=user.id,
        name="Groceries",
        cost=Decimal(cost),
        frequency=Frequency.MONTHLY,
        period=current_period(),
    )
    db.add(expense)
    await db.commit()
    return user, expense


async def _post(
    db: AsyncSession, user: User, expense: Expense | None, type_: TransactionType, amount: str
) -> list[Transaction]:
    return await transaction_service.create_transaction(
        db,
        user,
        TransactionCreate(
            expense_id=expense.id if expense else None,
            type=type_,
            name=f"{type_.value} {amount}",
            amount=Decimal(amount),
            date=datetime.date.today(),
        ),
    )


# ---------------------------------------------------------------------------
# the fund comes into being on the first save
# ---------------------------------------------------------------------------


async def test_an_expense_starts_with_no_fund(db_session: AsyncSession) -> None:
    """The control. Most Expenses never save anything, which is why the column is
    nullable rather than a fund per Expense."""
    _, expense = await _expense(db_session)

    assert expense.savings_id is None
    assert await savings_service.spendable(db_session, expense) == Decimal(0)


async def test_a_save_creates_the_fund(db_session: AsyncSession) -> None:
    """A fund exists because money went into it, never because an Expense was created."""
    user, expense = await _expense(db_session)

    await _post(db_session, user, expense, TransactionType.SAVE, "100.00")

    assert expense.savings_id is not None
    assert await savings_service.balance(db_session, expense.savings_id) == Decimal("100.00")


async def test_a_second_save_reuses_the_same_fund(db_session: AsyncSession) -> None:
    user, expense = await _expense(db_session)

    await _post(db_session, user, expense, TransactionType.SAVE, "100.00")
    first = expense.savings_id
    await _post(db_session, user, expense, TransactionType.SAVE, "50.00")

    assert expense.savings_id == first
    assert await savings_service.balance(db_session, first) == Decimal("150.00")


# ---------------------------------------------------------------------------
# the balance is derived, not stored
# ---------------------------------------------------------------------------


async def test_soft_deleting_a_save_removes_it_from_the_balance(
    db_session: AsyncSession,
) -> None:
    """The point of deriving: there is no second copy to forget to update."""
    user, expense = await _expense(db_session)
    (saved,) = await _post(db_session, user, expense, TransactionType.SAVE, "100.00")

    await transaction_service.soft_delete_transaction(db_session, saved)

    assert await savings_service.balance(db_session, expense.savings_id) == Decimal(0)


async def test_income_and_plain_spend_never_touch_a_fund(db_session: AsyncSession) -> None:
    """Only SAVE, SPEND_SAVED and TRANSFER move a fund."""
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SAVE, "100.00")

    await _post(db_session, user, None, TransactionType.INCOME, "2000.00")

    assert await savings_service.balance(db_session, expense.savings_id) == Decimal("100.00")


async def test_a_fund_belongs_to_the_lineage_not_one_period(db_session: AsyncSession) -> None:
    """Next Period's row points at the same fund, and its saves accumulate into it. This
    is the grain problem `amount_saved` had: a balance spans Periods, a row does not."""
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SAVE, "100.00")

    # What Rollover does: a new row for the next Period carrying the same savings_id.
    next_period = Expense(
        budget_id=expense.budget_id,
        category_id=expense.category_id,
        user_id=user.id,
        name=expense.name,
        cost=expense.cost,
        frequency=Frequency.MONTHLY,
        period=(current_period() + datetime.timedelta(days=32)).replace(day=1),
        savings_id=expense.savings_id,
    )
    db_session.add(next_period)
    await db_session.commit()
    await _post(db_session, user, next_period, TransactionType.SAVE, "100.00")

    assert await savings_service.balance(db_session, expense.savings_id) == Decimal("200.00")


async def test_the_fund_is_stamped_on_the_movements_that_move_it(
    db_session: AsyncSession,
) -> None:
    """`savings_id` is non-null on exactly the rows that move a fund. The Pool half of a
    split carries none, which is the invariant the balance sum relies on."""
    user, expense = await _expense(db_session)
    (saved,) = await _post(db_session, user, expense, TransactionType.SAVE, "30.00")
    rows = await _post(db_session, user, expense, TransactionType.SPEND, "50.00")

    by_type = {r.type: r for r in rows}
    assert saved.savings_id == expense.savings_id
    assert by_type[TransactionType.SPEND_SAVED].savings_id == expense.savings_id
    assert by_type[TransactionType.SPEND].savings_id is None


async def test_a_plain_spend_is_never_stamped(db_session: AsyncSession) -> None:
    """It drew on the Pool, not a fund - so it must not appear in any balance."""
    user, expense = await _expense(db_session)

    (spend,) = await _post(db_session, user, expense, TransactionType.SPEND, "50.00")

    assert spend.savings_id is None


async def test_income_is_never_stamped(db_session: AsyncSession) -> None:
    user, _ = await _expense(db_session)

    (income,) = await _post(db_session, user, None, TransactionType.INCOME, "2000.00")

    assert income.savings_id is None


# ---------------------------------------------------------------------------
# a balance counts money, not visible rows
# ---------------------------------------------------------------------------


async def test_a_balance_survives_its_expense_being_soft_deleted(
    db_session: AsyncSession,
) -> None:
    """The reason the fund is recorded on the Transaction rather than derived through
    the Expense. Deriving it made this read zero while the money was still real -
    a silent revaluation rather than a hidden row."""
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SAVE, "100.00")

    expense.deleted_at = func.now()
    await db_session.commit()

    assert await savings_service.balance(db_session, expense.savings_id) == Decimal("100.00")


async def test_a_balance_survives_its_budget_being_soft_deleted(
    db_session: AsyncSession,
) -> None:
    """Same reasoning one level up. `live_transactions` hides both, and hiding a row is
    the wrong response to a question about how much money exists."""
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SAVE, "100.00")

    budget = await db_session.get(Budget, expense.budget_id)
    assert budget is not None
    budget.deleted_at = func.now()
    await db_session.commit()

    assert await savings_service.balance(db_session, expense.savings_id) == Decimal("100.00")


async def test_repointing_a_lineage_leaves_history_where_it_happened(
    db_session: AsyncSession,
) -> None:
    """What the reallocation map will do one day. The old fund keeps the money that went
    into it; deriving through `Expense.savings_id` would have moved all of it to the new
    fund and emptied the old one."""
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SAVE, "100.00")
    original = expense.savings_id

    replacement = Savings(user_id=user.id)
    db_session.add(replacement)
    await db_session.flush()
    expense.savings_id = replacement.id
    await db_session.commit()

    assert await savings_service.balance(db_session, original) == Decimal("100.00")
    assert await savings_service.balance(db_session, replacement.id) == Decimal(0)


# ---------------------------------------------------------------------------
# the split
# ---------------------------------------------------------------------------


async def test_a_spend_with_no_fund_is_one_row(db_session: AsyncSession) -> None:
    user, expense = await _expense(db_session)

    rows = await _post(db_session, user, expense, TransactionType.SPEND, "50.00")

    assert [(r.type, r.amount) for r in rows] == [(TransactionType.SPEND, Decimal("50.00"))]


async def test_a_partly_covered_spend_splits_fund_first(db_session: AsyncSession) -> None:
    """The headline case: 50 spent against a fund holding 30 is 30 from the fund and 20
    from the Pool."""
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SAVE, "30.00")

    rows = await _post(db_session, user, expense, TransactionType.SPEND, "50.00")

    assert {(r.type, r.amount) for r in rows} == {
        (TransactionType.SPEND_SAVED, Decimal("30.00")),
        (TransactionType.SPEND, Decimal("20.00")),
    }
    assert await savings_service.balance(db_session, expense.savings_id) == Decimal(0)


async def test_a_fully_covered_spend_produces_no_pool_row(db_session: AsyncSession) -> None:
    """Which is why an Expense's Met can move by less than the amount spent: the pool
    row is what counts toward Met, and here there isn't one."""
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SAVE, "100.00")

    rows = await _post(db_session, user, expense, TransactionType.SPEND, "40.00")

    assert [(r.type, r.amount) for r in rows] == [(TransactionType.SPEND_SAVED, Decimal("40.00"))]
    assert await savings_service.balance(db_session, expense.savings_id) == Decimal("60.00")


async def test_a_spend_never_overdraws_the_fund(db_session: AsyncSession) -> None:
    """`spendable` caps the draw at what is actually there - the invariant that cannot
    be a CHECK, because it spans rows."""
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SAVE, "10.00")

    await _post(db_session, user, expense, TransactionType.SPEND, "500.00")

    assert await savings_service.balance(db_session, expense.savings_id) == Decimal(0)


async def test_an_emptied_fund_stops_splitting(db_session: AsyncSession) -> None:
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SAVE, "30.00")
    await _post(db_session, user, expense, TransactionType.SPEND, "30.00")

    rows = await _post(db_session, user, expense, TransactionType.SPEND, "25.00")

    assert [r.type for r in rows] == [TransactionType.SPEND]


# ---------------------------------------------------------------------------
# server-only types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("type_", [TransactionType.SPEND_SAVED, TransactionType.TRANSFER])
async def test_server_only_types_are_rejected_at_the_schema(type_: TransactionType) -> None:
    """Rejected before any service runs, so there is no path that reaches the ledger."""
    with pytest.raises(ValueError, match="written by the server"):
        TransactionCreate(
            expense_id=uuid.uuid4(),
            type=type_,
            name="Nope",
            amount=Decimal("1.00"),
            date=datetime.date.today(),
        )


async def test_a_fund_survives_its_expense_row_being_deleted_from_under_it(
    db_session: AsyncSession,
) -> None:
    """ON DELETE SET NULL, not CASCADE: an Expense outliving its fund reference is the
    right outcome, because losing the Expense would take its Transactions with it."""
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SAVE, "100.00")
    savings_id = expense.savings_id

    await db_session.delete(await db_session.get(Savings, savings_id))
    await db_session.commit()
    await db_session.refresh(expense)

    assert expense.savings_id is None


# ---------------------------------------------------------------------------
# closing a fund when its live Expense is deleted
# ---------------------------------------------------------------------------


async def _transactions(db: AsyncSession, user: User) -> list[Transaction]:
    """Every row the user has, visible or not - the drain writes one row against a
    just-deleted Expense, which `live_transactions` would hide."""
    result = await db.scalars(
        select(Transaction).where(Transaction.user_id == user.id).order_by(Transaction.name)
    )
    return list(result.all())


def _live_pool(rows: list[Transaction]) -> Decimal:
    """The Pool over undeleted rows: INCOME - SAVE - SPEND + Pool-side TRANSFER."""
    total = Decimal(0)
    for t in rows:
        if t.deleted_at is not None:
            continue
        if t.type is TransactionType.INCOME:
            total += t.amount
        elif t.type in (TransactionType.SAVE, TransactionType.SPEND):
            total -= t.amount
        elif t.type is TransactionType.TRANSFER and t.expense_id is None:
            total += t.amount
    return total


async def _fund_from_last_period(
    db: AsyncSession, user: User, expense: Expense, amount: str
) -> None:
    """Give `expense`'s lineage a fund holding `amount` saved in the previous Period.

    Deleting an Expense withdraws its own Period's Saves (docs/adr/0014), so only money
    from earlier Periods is left for the drain to move. A test about the drain needs
    that money to exist, or it passes by transferring nothing.
    """
    await db.refresh(expense)
    previous = Expense(
        budget_id=expense.budget_id,
        category_id=expense.category_id,
        user_id=user.id,
        name=expense.name,
        cost=expense.cost,
        frequency=expense.frequency,
        period=(expense.period - datetime.timedelta(days=1)).replace(day=1),
        series_id=expense.series_id,
    )
    db.add(previous)
    await db.commit()
    await _post(db, user, previous, TransactionType.SAVE, amount)
    await db.refresh(previous)
    expense.savings_id = previous.savings_id
    await db.commit()


async def test_deleting_the_live_expense_drains_the_fund(db_session: AsyncSession) -> None:
    """Money set aside for a plan that no longer exists belongs back in the Pool, not
    stranded behind a deleted row."""
    user, expense = await _expense(db_session)
    await _fund_from_last_period(db_session, user, expense, "100.00")

    await expense_service.soft_delete_expense(db_session, expense)

    assert await savings_service.balance(db_session, expense.savings_id) == Decimal(0)


async def test_the_drain_is_a_transfer_pair(db_session: AsyncSession) -> None:
    """One row on the fund side, one on the Pool side, linked by `transfer_id`.
    Direction is readable off `expense_id`/`savings_id`, so the link carries no meaning
    beyond pairing them."""
    user, expense = await _expense(db_session)
    await _fund_from_last_period(db_session, user, expense, "100.00")
    savings_id = expense.savings_id

    await expense_service.soft_delete_expense(db_session, expense)

    transfers = [
        t for t in await _transactions(db_session, user) if t.type is TransactionType.TRANSFER
    ]
    assert len(transfers) == 2
    fund_side = next(t for t in transfers if t.savings_id is not None)
    pool_side = next(t for t in transfers if t.savings_id is None)

    assert fund_side.expense_id == expense.id
    assert fund_side.savings_id == savings_id
    assert fund_side.transfer_id is None
    assert pool_side.expense_id is None
    assert pool_side.transfer_id == fund_side.id
    assert fund_side.amount == pool_side.amount == Decimal("100.00")


async def test_the_drain_is_not_income(db_session: AsyncSession) -> None:
    """The tempting shortcut, and wrong: income asserts money arrived from outside, and
    nothing arrived. It would put the account above the bank by the fund's value."""
    user, expense = await _expense(db_session)
    await _fund_from_last_period(db_session, user, expense, "100.00")

    await expense_service.soft_delete_expense(db_session, expense)

    assert not [
        t for t in await _transactions(db_session, user) if t.type is TransactionType.INCOME
    ]


async def test_the_fund_is_closed_not_merely_emptied(db_session: AsyncSession) -> None:
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SAVE, "100.00")

    await expense_service.soft_delete_expense(db_session, expense)

    savings = await db_session.get(Savings, expense.savings_id)
    assert savings is not None
    assert savings.deleted_at is not None


async def test_an_empty_fund_is_closed_without_a_transfer(db_session: AsyncSession) -> None:
    """Nothing to move, but the fund still ends - a zero-amount transfer pair would be
    two rows recording that nothing happened."""
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SAVE, "50.00")
    await _post(db_session, user, expense, TransactionType.SPEND, "50.00")

    await expense_service.soft_delete_expense(db_session, expense)

    savings = await db_session.get(Savings, expense.savings_id)
    assert savings is not None and savings.deleted_at is not None
    assert not [
        t for t in await _transactions(db_session, user) if t.type is TransactionType.TRANSFER
    ]


async def test_deleting_an_expense_with_no_fund_writes_nothing(
    db_session: AsyncSession,
) -> None:
    """The common case: most Expenses never save anything."""
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SPEND, "20.00")

    await expense_service.soft_delete_expense(db_session, expense)

    # `deleted_at` was assigned `func.now()`, which leaves the attribute expired after
    # the flush - `eager_defaults` doesn't fetch it back - so it has to be reloaded
    # before a plain attribute read, or the async session raises MissingGreenlet.
    await db_session.refresh(expense)
    assert expense.deleted_at is not None
    assert len(await _transactions(db_session, user)) == 1


async def test_a_closed_fund_is_not_drained_twice(db_session: AsyncSession) -> None:
    """Idempotent through the `deleted_at` check. A second drain would invent money."""
    user, expense = await _expense(db_session)
    await _fund_from_last_period(db_session, user, expense, "100.00")
    await expense_service.soft_delete_expense(db_session, expense)

    await savings_service.close_savings(db_session, expense)
    await db_session.commit()

    transfers = [
        t for t in await _transactions(db_session, user) if t.type is TransactionType.TRANSFER
    ]
    assert len(transfers) == 2


async def test_the_drain_conserves_money(db_session: AsyncSession) -> None:
    """The whole point. Pool + fund is unchanged across the delete: the Pool regains
    exactly what the fund gives up, and Met is untouched because transfers never count.
    """
    user, expense = await _expense(db_session)
    await _post(db_session, user, None, TransactionType.INCOME, "500.00")
    await _fund_from_last_period(db_session, user, expense, "100.00")

    before = _live_pool(await _transactions(db_session, user)) + await savings_service.balance(
        db_session, expense.savings_id
    )
    await expense_service.soft_delete_expense(db_session, expense)
    after = _live_pool(await _transactions(db_session, user)) + await savings_service.balance(
        db_session, expense.savings_id
    )

    assert before == after == Decimal("500.00")


# ---------------------------------------------------------------------------
# withdraw before drain (docs/adr/0014)
# ---------------------------------------------------------------------------
# Deleting an Expense withdraws its Transactions *and then* drains the fund. The
# other order double-counts: the drain reads a balance that still includes this
# Period's Saves and returns them to the Pool as a Transfer, and the withdrawal then
# returns them again. It also withdraws the fund side of the Transfer it just wrote
# (that row carries the Expense's `expense_id`), stranding the Pool side on its own.


async def test_a_save_from_this_period_is_withdrawn_not_drained(
    db_session: AsyncSession,
) -> None:
    """The fund holds only this Period's money, so withdrawing the Save empties it and
    there is nothing left to Transfer. The Pool ends exactly where it was before the
    Save, not above it."""
    user, expense = await _expense(db_session)
    pool_before_save = _live_pool(await _transactions(db_session, user))
    await _post(db_session, user, expense, TransactionType.SAVE, "50.00")

    await expense_service.soft_delete_expense(db_session, expense)

    rows = await _transactions(db_session, user)
    assert not [t for t in rows if t.type is TransactionType.TRANSFER]
    assert _live_pool(rows) == pool_before_save
    assert await savings_service.balance(db_session, expense.savings_id) == Decimal(0)


async def test_only_money_from_earlier_periods_is_drained(db_session: AsyncSession) -> None:
    """Earlier Periods' Saves stay on the record and leave the fund as a Transfer; this
    Period's Save is withdrawn. Both halves of the Transfer survive the delete - the
    withdrawal must not catch the fund side it wrote."""
    user, expense = await _expense(db_session)
    await _fund_from_last_period(db_session, user, expense, "100.00")
    await _post(db_session, user, expense, TransactionType.SAVE, "30.00")

    rows = await _transactions(db_session, user)
    held_before = await savings_service.balance(db_session, expense.savings_id)
    total_before = _live_pool(rows) + held_before
    assert held_before == Decimal("130.00")

    await expense_service.soft_delete_expense(db_session, expense)

    rows = await _transactions(db_session, user)
    transfers = [t for t in rows if t.type is TransactionType.TRANSFER]
    assert len(transfers) == 2
    assert all(t.deleted_at is None for t in transfers)
    assert {t.amount for t in transfers} == {Decimal("100.00")}
    this_period_save = next(t for t in rows if t.amount == Decimal("30.00"))
    assert this_period_save.deleted_at is not None
    held_after = await savings_service.balance(db_session, expense.savings_id)
    assert held_after == Decimal(0)
    assert _live_pool(rows) + held_after == total_before
