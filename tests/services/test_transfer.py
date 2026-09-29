"""User-requested Transfers: fund to Pool, fund to fund, and what survives a delete.

Against a real Postgres. A fund's balance is derived through `expense_id` (ADR-0015) and
withdrawal matches on `deleted_at` (ADR-0014), neither of which a mocked session can see.

Every test ends in `_assert_books_balance`, which checks two things that together catch
every way this design was found to go wrong (docs/adr/0016):

- **The books balance**: Pool + every fund = Income - Spend - Spend Saved. Every row is
  one side of a double entry, so this only breaks when half a movement is counted - a
  Transfer pair with one row withdrawn and the other not.
- **No fund is below zero**. Withdrawing a Save whose money has since been Transferred
  out still balances the books - it does so by leaving the fund negative, which is money
  in the Pool that exists nowhere else.
"""

import datetime
import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import DeletedRow
from app.models.enums import TransactionType
from app.models.expense import Expense
from app.models.savings import Savings
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.transaction import TransactionCreate, TransactionUpdate
from app.services import expense as expense_service
from app.services import savings as savings_service
from app.services import transaction as transaction_service
from app.services.savings import FundOverdrawn
from app.services.transaction import InvalidTransfer, RestoreBlocked, TransferLocked
from tests.services.test_savings import (
    _expense,
    _fund_from_last_period,
    _live_pool,
    _post,
    _transactions,
)

TODAY = datetime.date.today()
_ANY_ID = uuid.UUID(int=1)
_OTHER_ID = uuid.UUID(int=2)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _sibling(db: AsyncSession, user: User, like: Expense, name: str) -> Expense:
    """Another current-Period Expense in the same Budget, with no fund."""
    expense = Expense(
        budget_id=like.budget_id,
        category_id=like.category_id,
        user_id=user.id,
        name=name,
        cost=Decimal("100.00"),
        frequency=like.frequency,
        period=like.period,
    )
    db.add(expense)
    await db.commit()
    return expense


async def _funded(db: AsyncSession, amount: str = "100.00") -> tuple[User, Expense, Expense]:
    """A User with A holding `amount` from last Period, and an unfunded B beside it."""
    user, a = await _expense(db)
    await _fund_from_last_period(db, user, a, amount)
    b = await _sibling(db, user, a, "B")
    return user, a, b


async def _transfer(
    db: AsyncSession,
    user: User,
    source: Expense,
    destination: Expense | None,
    amount: str,
) -> list[Transaction]:
    return await transaction_service.create_transaction(
        db,
        user,
        TransactionCreate(
            expense_id=source.id,
            to_expense_id=destination.id if destination else None,
            type=TransactionType.TRANSFER,
            name="Move",
            amount=Decimal(amount),
            date=TODAY,
        ),
    )


async def _fund(db: AsyncSession, expense: Expense) -> Decimal:
    await db.refresh(expense)
    if expense.savings_id is None:
        return Decimal(0)
    return await savings_service.balance(db, expense.savings_id)


async def _pool(db: AsyncSession, user: User) -> Decimal:
    await db.refresh(user)  # in case a refusal's rollback expired it
    return _live_pool(await _transactions(db, user))


async def _met_progress(db: AsyncSession, expense: Expense) -> Decimal:
    """What counts toward Met: this row's undeleted Save and Spend."""
    rows = await db.scalars(
        select(Transaction).where(
            Transaction.expense_id == expense.id,
            Transaction.deleted_at.is_(None),
            Transaction.type.in_([TransactionType.SAVE, TransactionType.SPEND]),
        )
    )
    return sum((t.amount for t in rows.all()), Decimal(0))


async def _assert_books_balance(db: AsyncSession, user: User) -> None:
    # Many tests call this after a refusal, whose rollback expires every loaded row.
    await db.refresh(user)
    rows = [t for t in await _transactions(db, user) if t.deleted_at is None]
    funds = (await db.scalars(select(Savings.id).where(Savings.user_id == user.id))).all()
    balances = [await savings_service.balance(db, fund) for fund in funds]

    assert all(b >= 0 for b in balances), f"a fund went negative: {balances}"

    def total(*types: TransactionType) -> Decimal:
        return sum((t.amount for t in rows if t.type in types), Decimal(0))

    left_the_account = total(TransactionType.SPEND, TransactionType.SPEND_SAVED)
    assert _live_pool(rows) + sum(balances, Decimal(0)) == (
        total(TransactionType.INCOME) - left_the_account
    )


async def _delete_expense(db: AsyncSession, expense: Expense) -> None:
    await expense_service.soft_delete_expense(db, expense)
    await db.refresh(expense)


async def _group(db: AsyncSession, anchor: Transaction) -> list[Transaction]:
    # A refused change rolls back, which expires every loaded row.
    await db.refresh(anchor)
    rows = await db.scalars(select(Transaction).where(Transaction.transfer_id == anchor.id))
    return list(rows.all())


# ---------------------------------------------------------------------------
# 1. fund to Pool
# ---------------------------------------------------------------------------


async def test_a_transfer_to_the_pool_is_a_linked_pair(db_session: AsyncSession) -> None:
    user, a, _ = await _funded(db_session)
    pool_before = await _pool(db_session, user)

    anchor, pool_side = await _transfer(db_session, user, a, None, "40.00")

    assert (anchor.type, anchor.expense_id, anchor.transfer_id) == (
        TransactionType.TRANSFER,
        a.id,
        anchor.id,
    )
    assert (pool_side.type, pool_side.expense_id, pool_side.transfer_id) == (
        TransactionType.TRANSFER,
        None,
        anchor.id,
    )
    assert await _fund(db_session, a) == Decimal("60.00")
    assert await _pool(db_session, user) == pool_before + Decimal("40.00")
    assert await _met_progress(db_session, a) == Decimal(0)
    await _assert_books_balance(db_session, user)


# ---------------------------------------------------------------------------
# 2. fund to fund
# ---------------------------------------------------------------------------


async def test_a_transfer_between_funds_is_three_rows_through_the_pool(
    db_session: AsyncSession,
) -> None:
    """The Pool side is what stops the Save debiting a Pool that was never credited."""
    user, a, b = await _funded(db_session)
    pool_before = await _pool(db_session, user)

    anchor, pool_side, save = await _transfer(db_session, user, a, b, "40.00")

    assert pool_side.transfer_id == anchor.id
    assert (save.type, save.expense_id, save.transfer_id) == (
        TransactionType.SAVE,
        b.id,
        anchor.id,
    )
    assert await _fund(db_session, a) == Decimal("60.00")
    assert await _fund(db_session, b) == Decimal("40.00")
    assert await _pool(db_session, user) == pool_before
    assert await _met_progress(db_session, b) == Decimal("40.00")
    assert await _met_progress(db_session, a) == Decimal(0)
    await _assert_books_balance(db_session, user)


async def test_every_row_of_a_transfer_shares_one_transfer_id(
    db_session: AsyncSession,
) -> None:
    """The anchor's own id, anchor included. Each Transfer gets its own, and rows
    outside any Transfer have none."""
    user, a, b = await _funded(db_session)

    first = await _transfer(db_session, user, a, b, "40.00")
    second = await _transfer(db_session, user, a, None, "10.00")
    (spend,) = await _post(db_session, user, b, TransactionType.SPEND, "5.00")

    assert {t.transfer_id for t in first} == {first[0].id}
    assert {t.transfer_id for t in second} == {second[0].id}
    assert spend.transfer_id is None


async def test_a_save_added_by_an_edit_joins_the_group(db_session: AsyncSession) -> None:
    user, a, b = await _funded(db_session)
    anchor, _ = await _transfer(db_session, user, a, None, "40.00")

    await _edit(db_session, user, anchor, to_expense_id=b.id)

    group = [t for t in await _group(db_session, anchor) if t.deleted_at is None]
    assert {t.type for t in group} == {TransactionType.TRANSFER, TransactionType.SAVE}
    assert len(group) == 3


# ---------------------------------------------------------------------------
# 3. what a Transfer refuses
# ---------------------------------------------------------------------------


async def test_a_transfer_cannot_take_more_than_the_fund_holds(
    db_session: AsyncSession,
) -> None:
    user, a, _ = await _funded(db_session)

    with pytest.raises(InvalidTransfer, match="holds 100.00"):
        await _transfer(db_session, user, a, None, "100.01")


async def test_a_transfer_needs_a_fund_to_draw_on(db_session: AsyncSession) -> None:
    user, a, b = await _funded(db_session)

    with pytest.raises(InvalidTransfer, match="no savings"):
        await _transfer(db_session, user, b, a, "1.00")


@pytest.mark.parametrize("amount", ["0", "-5.00"])
async def test_a_transfer_amount_must_be_positive(amount: str) -> None:
    """Negative would run Pool to fund, which ADR-0011 made unrepresentable."""
    with pytest.raises(ValidationError, match="positive"):
        TransactionCreate(
            expense_id=_ANY_ID,
            type=TransactionType.TRANSFER,
            name="Move",
            amount=Decimal(amount),
            date=TODAY,
        )


async def test_a_transfer_cannot_go_to_its_own_source() -> None:
    with pytest.raises(ValidationError, match="same Expense"):
        TransactionCreate(
            expense_id=_ANY_ID,
            to_expense_id=_ANY_ID,
            type=TransactionType.TRANSFER,
            name="Move",
            amount=Decimal("1.00"),
            date=TODAY,
        )


async def test_only_a_transfer_takes_a_destination() -> None:
    with pytest.raises(ValidationError, match="only a transfer"):
        TransactionCreate(
            expense_id=_ANY_ID,
            to_expense_id=_OTHER_ID,
            type=TransactionType.SPEND,
            name="Shop",
            amount=Decimal("1.00"),
            date=TODAY,
        )


async def test_a_transfer_cannot_draw_on_an_earlier_periods_row(
    db_session: AsyncSession,
) -> None:
    """Withdraw-on-delete and Restore key on the current row's `expense_id`."""
    user, a, _ = await _funded(db_session)
    previous = (
        await db_session.scalars(
            select(Expense).where(Expense.series_id == a.series_id, Expense.id != a.id)
        )
    ).one()

    with pytest.raises(InvalidTransfer, match="current Period"):
        await _transfer(db_session, user, previous, None, "1.00")


async def test_a_transfer_cannot_go_to_a_deleted_expense(db_session: AsyncSession) -> None:
    user, a, b = await _funded(db_session)
    await _delete_expense(db_session, b)

    with pytest.raises(DeletedRow):
        await _transfer(db_session, user, a, b, "1.00")


# ---------------------------------------------------------------------------
# 4-7. deleting an Expense withdraws its side of every Transfer
# ---------------------------------------------------------------------------
#
# Everything on the deleted Expense goes, plus the Pool side of each Transfer out of
# it. A destination's Save on *another* Expense stays: that money was moved there.


async def _locked(db: AsyncSession, user: User, row: Transaction) -> None:
    """Both ways of changing the group are refused."""
    await db.refresh(row)
    with pytest.raises(TransferLocked):
        await transaction_service.update_transaction(
            db, row, TransactionUpdate(amount=Decimal("1.00")), user
        )
    with pytest.raises(TransferLocked):
        await transaction_service.soft_delete_transaction(db, row)


async def test_deleting_the_source_withdraws_its_transfer_and_drains_the_rest(
    db_session: AsyncSession,
) -> None:
    """Both halves go together - withdrawing only the anchor would leave the Pool side
    counting money the drain then returns a second time."""
    user, a, _ = await _funded(db_session)
    pool_before = await _pool(db_session, user)
    anchor, pool_side = await _transfer(db_session, user, a, None, "40.00")

    await _delete_expense(db_session, a)

    for row in (anchor, pool_side):
        await db_session.refresh(row)
        assert row.deleted_at == a.deleted_at
    assert await _fund(db_session, a) == Decimal(0)
    assert await _pool(db_session, user) == pool_before + Decimal("100.00")
    await _assert_books_balance(db_session, user)


async def test_deleting_the_source_leaves_the_destination_its_money(
    db_session: AsyncSession,
) -> None:
    """A holds 100 from last Period and sends 60 to B. Deleting A drains all 100 of its
    own fund, and B keeps its 60 - as if it had come straight from the Pool."""
    user, a, b = await _funded(db_session)
    pool_before = await _pool(db_session, user)
    _, _, save = await _transfer(db_session, user, a, b, "60.00")

    await _delete_expense(db_session, a)

    await db_session.refresh(save)
    assert save.deleted_at is None
    assert await _fund(db_session, a) == Decimal(0)
    assert await _fund(db_session, b) == Decimal("60.00")
    assert await _met_progress(db_session, b) == Decimal("60.00")
    assert await _pool(db_session, user) == pool_before + Decimal("40.00")
    await _assert_books_balance(db_session, user)
    # B's Save is live, but its anchor was withdrawn with A.
    await _locked(db_session, user, save)


async def test_deleting_a_source_whose_save_this_period_was_moved_out(
    db_session: AsyncSession,
) -> None:
    """The case that used to be refused. Withdrawing the Save alone would leave A at
    -100; the Transfer it paid for goes with it, so A ends at 0."""
    user, a = await _expense(db_session)
    await _post(db_session, user, None, TransactionType.INCOME, "100.00")
    await _post(db_session, user, a, TransactionType.SAVE, "100.00")
    await _transfer(db_session, user, a, None, "100.00")

    await _delete_expense(db_session, a)

    assert await _fund(db_session, a) == Decimal(0)
    assert await _pool(db_session, user) == Decimal("100.00")
    await _assert_books_balance(db_session, user)


async def test_deleting_a_fund_to_fund_source_whose_save_this_period_was_moved_out(
    db_session: AsyncSession,
) -> None:
    """Income 100, saved into A, moved on to B. Deleting A: B keeps the 100 and the Pool
    stays at 0 - the Income went to B, only the stop in A was a mistake."""
    user, a = await _expense(db_session)
    b = await _sibling(db_session, user, a, "B")
    await _post(db_session, user, None, TransactionType.INCOME, "100.00")
    await _post(db_session, user, a, TransactionType.SAVE, "100.00")
    await _transfer(db_session, user, a, b, "100.00")

    await _delete_expense(db_session, a)

    assert await _fund(db_session, a) == Decimal(0)
    assert await _fund(db_session, b) == Decimal("100.00")
    assert await _pool(db_session, user) == Decimal(0)
    await _assert_books_balance(db_session, user)

    await expense_service.restore_expense(db_session, a)

    assert await _fund(db_session, a) == Decimal(0)
    assert await _fund(db_session, b) == Decimal("100.00")
    assert await _pool(db_session, user) == Decimal(0)
    assert all(t.deleted_at is None for t in await _transactions(db_session, user))
    await _assert_books_balance(db_session, user)


async def _deleted(db: AsyncSession, rows: list[Transaction]) -> list[bool]:
    for row in rows:
        await db.refresh(row)
    return [row.deleted_at is not None for row in rows]


async def test_deleting_the_destination_undoes_the_transfer(
    db_session: AsyncSession,
) -> None:
    """All three rows go and A gets its money back. Restore puts the move back."""
    user, a, b = await _funded(db_session)
    pool_before = await _pool(db_session, user)
    rows = await _transfer(db_session, user, a, b, "60.00")

    await _delete_expense(db_session, b)

    assert await _deleted(db_session, rows) == [True, True, True]
    assert {row.deleted_at for row in rows} == {b.deleted_at}
    assert await _fund(db_session, a) == Decimal("100.00")
    assert await _fund(db_session, b) == Decimal(0)
    assert await _pool(db_session, user) == pool_before
    await _assert_books_balance(db_session, user)

    await expense_service.restore_expense(db_session, b)

    assert await _deleted(db_session, rows) == [False, False, False]
    assert await _fund(db_session, a) == Decimal("40.00")
    assert await _fund(db_session, b) == Decimal("60.00")
    assert await _pool(db_session, user) == pool_before
    await _assert_books_balance(db_session, user)


async def test_deleting_a_destination_that_moved_the_money_to_the_pool(
    db_session: AsyncSession,
) -> None:
    """A -> B, B -> Pool, delete B. Both Transfers are undone, so A has its money back
    and the Pool loses what B had sent it."""
    user, a, b = await _funded(db_session)
    pool_before = await _pool(db_session, user)
    into_b = await _transfer(db_session, user, a, b, "100.00")
    out_of_b = await _transfer(db_session, user, b, None, "100.00")

    await _delete_expense(db_session, b)

    assert await _deleted(db_session, [*into_b, *out_of_b]) == [True] * 5
    assert await _fund(db_session, a) == Decimal("100.00")
    assert await _fund(db_session, b) == Decimal(0)
    assert await _pool(db_session, user) == pool_before
    await _assert_books_balance(db_session, user)

    await expense_service.restore_expense(db_session, b)

    assert await _deleted(db_session, [*into_b, *out_of_b]) == [False] * 5
    assert await _fund(db_session, a) == Decimal(0)
    assert await _fund(db_session, b) == Decimal(0)
    assert await _pool(db_session, user) == pool_before + Decimal("100.00")
    await _assert_books_balance(db_session, user)


async def test_deleting_the_middle_of_a_chain_can_leave_the_pool_negative(
    db_session: AsyncSession,
) -> None:
    """A -> B -> C, delete B. A gets its 100 back and C keeps the 100 B gave it, so the
    Pool is over-allocated by 100. Accepted, as Restore accepts it (docs/adr/0016)."""
    user, a, b = await _funded(db_session)
    c = await _sibling(db_session, user, a, "C")
    pool_before = await _pool(db_session, user)
    into_b = await _transfer(db_session, user, a, b, "100.00")
    out_of_b = await _transfer(db_session, user, b, c, "100.00")

    await _delete_expense(db_session, b)

    assert await _deleted(db_session, [*into_b, *out_of_b]) == [True] * 5 + [False]
    assert await _fund(db_session, a) == Decimal("100.00")
    assert await _fund(db_session, c) == Decimal("100.00")
    assert await _pool(db_session, user) == pool_before - Decimal("100.00")
    await _assert_books_balance(db_session, user)

    await expense_service.restore_expense(db_session, b)

    assert await _fund(db_session, a) == Decimal(0)
    assert await _fund(db_session, b) == Decimal(0)
    assert await _fund(db_session, c) == Decimal("100.00")
    assert await _pool(db_session, user) == pool_before
    await _assert_books_balance(db_session, user)


async def test_a_transfer_re_pointed_away_from_the_destination_is_not_undone(
    db_session: AsyncSession,
) -> None:
    """The Save the edit removed from B still names the Transfer. Deleting B must not
    reach through it to a Transfer that now goes to C."""
    user, a, b = await _funded(db_session)
    c = await _sibling(db_session, user, a, "C")
    anchor, pool_side, _ = await _transfer(db_session, user, a, b, "60.00")
    await _edit(db_session, user, anchor, to_expense_id=c.id)

    await _delete_expense(db_session, b)

    assert await _deleted(db_session, [anchor, pool_side]) == [False, False]
    assert await _fund(db_session, a) == Decimal("40.00")
    assert await _fund(db_session, c) == Decimal("60.00")
    await _assert_books_balance(db_session, user)


async def test_deleting_the_source_then_the_destination(db_session: AsyncSession) -> None:
    """A's deletion already withdrew its side and drained A. B's deletion then takes
    only B's Save, and the money lands in the Pool - there is no A left to refund."""
    user, a, b = await _funded(db_session)
    pool_before = await _pool(db_session, user)
    anchor, pool_side, save = await _transfer(db_session, user, a, b, "60.00")

    await _delete_expense(db_session, a)
    await _delete_expense(db_session, b)

    await _deleted(db_session, [anchor, pool_side, save])
    assert (anchor.deleted_at, pool_side.deleted_at) == (a.deleted_at, a.deleted_at)
    assert save.deleted_at == b.deleted_at
    assert await _fund(db_session, a) == Decimal(0)
    assert await _fund(db_session, b) == Decimal(0)
    assert await _pool(db_session, user) == pool_before + Decimal("100.00")
    await _assert_books_balance(db_session, user)

    await expense_service.restore_expense(db_session, b)

    assert await _fund(db_session, b) == Decimal("60.00")
    assert await _pool(db_session, user) == pool_before + Decimal("40.00")
    await _assert_books_balance(db_session, user)


async def test_restoring_the_destination_waits_for_its_deleted_source(
    db_session: AsyncSession,
) -> None:
    """Delete B (A refunded), then A (drained, 100). Restoring B alone would put the
    row out of A back onto a closed fund that was drained without it."""
    user, a, b = await _funded(db_session)
    pool_before = await _pool(db_session, user)
    await _transfer(db_session, user, a, b, "60.00")
    await _delete_expense(db_session, b)
    await _delete_expense(db_session, a)

    with pytest.raises(RestoreBlocked, match="'Groceries'"):
        await expense_service.restore_expense(db_session, b)

    await db_session.rollback()
    await db_session.refresh(a)
    await db_session.refresh(b)
    await expense_service.restore_expense(db_session, a)
    await db_session.refresh(b)
    await expense_service.restore_expense(db_session, b)

    assert await _fund(db_session, a) == Decimal("40.00")
    assert await _fund(db_session, b) == Decimal("60.00")
    assert await _pool(db_session, user) == pool_before
    await _assert_books_balance(db_session, user)


# ---------------------------------------------------------------------------
# 8. editing a Transfer edits the group
# ---------------------------------------------------------------------------


async def _edit(db: AsyncSession, user: User, row: Transaction, **changes: object) -> Transaction:
    return await transaction_service.update_transaction(
        db, row, TransactionUpdate.model_validate(changes), user
    )


async def test_changing_the_amount_changes_every_row(db_session: AsyncSession) -> None:
    user, a, b = await _funded(db_session)
    anchor, pool_side, _ = await _transfer(db_session, user, a, b, "40.00")

    # Through the Pool side on purpose: any row of the group edits the group.
    await _edit(db_session, user, pool_side, amount=Decimal("70.00"))

    assert {t.amount for t in await _group(db_session, anchor)} == {Decimal("70.00")}
    assert await _fund(db_session, a) == Decimal("30.00")
    assert await _fund(db_session, b) == Decimal("70.00")
    await _assert_books_balance(db_session, user)


async def test_the_amount_is_checked_against_the_fund_plus_what_it_already_moved(
    db_session: AsyncSession,
) -> None:
    user, a, _ = await _funded(db_session)
    anchor, _ = await _transfer(db_session, user, a, None, "40.00")

    await _edit(db_session, user, anchor, amount=Decimal("100.00"))
    await db_session.refresh(anchor)

    with pytest.raises(InvalidTransfer, match="holds 100.00"):
        await _edit(db_session, user, anchor, amount=Decimal("100.01"))
    await _assert_books_balance(db_session, user)


async def test_shrinking_a_transfer_whose_money_was_spent_is_refused(
    db_session: AsyncSession,
) -> None:
    """Taking back part of B's Save when B already spent it would overdraw B."""
    user, a, b = await _funded(db_session)
    anchor, _, _ = await _transfer(db_session, user, a, b, "100.00")
    await _post(db_session, user, b, TransactionType.SPEND, "100.00")

    with pytest.raises(FundOverdrawn):
        await _edit(db_session, user, anchor, amount=Decimal("50.00"))

    assert {t.amount for t in await _group(db_session, anchor)} == {Decimal("100.00")}
    await _assert_books_balance(db_session, user)


async def test_name_note_and_date_spread_to_every_row(db_session: AsyncSession) -> None:
    user, a, b = await _funded(db_session)
    anchor, _, save = await _transfer(db_session, user, a, b, "40.00")
    yesterday = TODAY - datetime.timedelta(days=1)

    await _edit(db_session, user, save, name="Rainy day", note="car", date=yesterday)

    assert {(t.name, t.note, t.date) for t in await _group(db_session, anchor)} == {
        ("Rainy day", "car", yesterday)
    }


async def test_changing_the_source_moves_the_draw_to_the_new_fund(
    db_session: AsyncSession,
) -> None:
    user, a, b = await _funded(db_session)
    c = await _sibling(db_session, user, a, "C")
    await _post(db_session, user, c, TransactionType.SAVE, "50.00")
    anchor, pool_side = await _transfer(db_session, user, a, None, "40.00")

    await _edit(db_session, user, pool_side, expense_id=c.id)

    await db_session.refresh(anchor)
    await db_session.refresh(pool_side)
    assert anchor.expense_id == c.id
    assert pool_side.expense_id is None
    assert await _fund(db_session, a) == Decimal("100.00")
    assert await _fund(db_session, c) == Decimal("10.00")
    await _assert_books_balance(db_session, user)


async def test_changing_the_destination_moves_the_save(db_session: AsyncSession) -> None:
    user, a, b = await _funded(db_session)
    c = await _sibling(db_session, user, a, "C")
    anchor, _, save = await _transfer(db_session, user, a, b, "40.00")

    await _edit(db_session, user, anchor, to_expense_id=c.id)

    await db_session.refresh(save)
    assert save.expense_id == c.id
    assert await _fund(db_session, b) == Decimal(0)
    assert await _fund(db_session, c) == Decimal("40.00")
    assert await _met_progress(db_session, b) == Decimal(0)
    assert await _met_progress(db_session, c) == Decimal("40.00")
    await _assert_books_balance(db_session, user)


async def test_a_destination_of_the_pool_removes_the_save_and_back_adds_one(
    db_session: AsyncSession,
) -> None:
    user, a, b = await _funded(db_session)
    pool_before = await _pool(db_session, user)
    anchor, _, _ = await _transfer(db_session, user, a, b, "40.00")

    await _edit(db_session, user, anchor, to_expense_id=None)

    live = [t for t in await _group(db_session, anchor) if t.deleted_at is None]
    assert {t.type for t in live} == {TransactionType.TRANSFER}
    assert await _fund(db_session, b) == Decimal(0)
    assert await _pool(db_session, user) == pool_before + Decimal("40.00")
    await _assert_books_balance(db_session, user)

    await db_session.refresh(anchor)
    await _edit(db_session, user, anchor, to_expense_id=b.id)

    live = [t for t in await _group(db_session, anchor) if t.deleted_at is None]
    assert len(live) == 3
    assert await _fund(db_session, b) == Decimal("40.00")
    assert await _pool(db_session, user) == pool_before
    await _assert_books_balance(db_session, user)


async def test_a_transfer_row_cannot_change_type(db_session: AsyncSession) -> None:
    user, a, b = await _funded(db_session)
    anchor, _, save = await _transfer(db_session, user, a, b, "40.00")

    with pytest.raises(InvalidTransfer, match="type"):
        await _edit(db_session, user, anchor, type=TransactionType.SPEND)
    await db_session.refresh(save)
    with pytest.raises(InvalidTransfer, match="type"):
        await _edit(db_session, user, save, type=TransactionType.SPEND)


async def test_an_ordinary_row_cannot_become_a_transfer(db_session: AsyncSession) -> None:
    """Otherwise a PATCH writes half a Transfer."""
    user, a, b = await _funded(db_session)
    (spend,) = await _post(db_session, user, b, TransactionType.SPEND, "10.00")

    with pytest.raises(InvalidTransfer, match="type"):
        await _edit(db_session, user, spend, type=TransactionType.TRANSFER)
    await db_session.refresh(spend)
    with pytest.raises(InvalidTransfer, match="only a transfer"):
        await _edit(db_session, user, spend, to_expense_id=a.id)


# ---------------------------------------------------------------------------
# 9. deleting any row deletes the group
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", [0, 1, 2])
async def test_deleting_any_row_deletes_the_whole_transfer(
    db_session: AsyncSession, which: int
) -> None:
    user, a, b = await _funded(db_session)
    pool_before = await _pool(db_session, user)
    rows = await _transfer(db_session, user, a, b, "40.00")

    await transaction_service.soft_delete_transaction(db_session, rows[which])

    group = await _group(db_session, rows[0])
    assert len(group) == 3
    assert len({t.deleted_at for t in group}) == 1
    assert group[0].deleted_at is not None
    assert await _fund(db_session, a) == Decimal("100.00")
    assert await _fund(db_session, b) == Decimal(0)
    assert await _pool(db_session, user) == pool_before
    await _assert_books_balance(db_session, user)


async def test_deleting_a_transfer_whose_money_was_spent_is_refused(
    db_session: AsyncSession,
) -> None:
    user, a, b = await _funded(db_session)
    anchor, _, _ = await _transfer(db_session, user, a, b, "100.00")
    await _post(db_session, user, b, TransactionType.SPEND, "60.00")

    with pytest.raises(FundOverdrawn) as refused:
        await transaction_service.soft_delete_transaction(db_session, anchor)

    assert refused.value.status_code == 409
    assert all(t.deleted_at is None for t in await _group(db_session, anchor))
    await _assert_books_balance(db_session, user)


# ---------------------------------------------------------------------------
# 10. Restore brings the Transfer back, Pool side included
# ---------------------------------------------------------------------------


async def test_restore_brings_back_both_halves_of_a_transfer(db_session: AsyncSession) -> None:
    """The Pool side has no `expense_id`, so Restore has to find it through its anchor -
    otherwise the fund's money comes back and the Pool keeps the drain's copy of it."""
    user, a, _ = await _funded(db_session)
    pool_before = await _pool(db_session, user)
    anchor, pool_side = await _transfer(db_session, user, a, None, "40.00")
    await _delete_expense(db_session, a)

    await expense_service.restore_expense(db_session, a)

    for row in (anchor, pool_side):
        await db_session.refresh(row)
        assert row.deleted_at is None
    assert await _fund(db_session, a) == Decimal("60.00")
    assert await _pool(db_session, user) == pool_before + Decimal("40.00")
    await _assert_books_balance(db_session, user)
