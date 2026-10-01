"""Transactions are written only in the open Period (docs/adr/0017).

Against a real Postgres for the edit gate, which reads `created_at` - a value only the
database sets - and for the Expense check, which needs a real row in a closed Period.
"""

import datetime
import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import TransactionType
from app.models.expense import Expense
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.fields import current_period, today
from app.schemas.transaction import (
    BulkTransactionCreate,
    TransactionCreate,
    TransactionUpdate,
)
from app.services import transaction as transaction_service
from app.services.transaction import (
    BulkItemFailed,
    ExpenseInClosedPeriod,
    RecordedInClosedPeriod,
)
from tests.services.test_savings import _expense, _fund_from_last_period, _post

_ANY_ID = uuid.UUID(int=1)


def _create(**overrides: object) -> TransactionCreate:
    fields: dict[str, object] = {
        "expense_id": _ANY_ID,
        "type": TransactionType.SPEND,
        "name": "Shop",
        "amount": Decimal("1.00"),
        "date": today(),
    }
    return TransactionCreate.model_validate(fields | overrides)


async def _previous(db: AsyncSession, user: User, expense: Expense) -> Expense:
    """Last Period's row of `expense`'s lineage, holding 100 in its fund."""
    await _fund_from_last_period(db, user, expense, "100.00")
    return (
        await db.scalars(
            select(Expense).where(
                Expense.series_id == expense.series_id, Expense.id != expense.id
            )
        )
    ).one()


async def _recorded_last_period(db: AsyncSession, *rows: Transaction) -> None:
    """Backdate when `rows` were recorded, as if entered last Period."""
    last_month = datetime.datetime.combine(
        current_period() - datetime.timedelta(days=1), datetime.time(12), datetime.UTC
    )
    await db.execute(
        update(Transaction)
        .where(Transaction.id.in_([r.id for r in rows]))
        .values(created_at=last_month)
    )
    await db.commit()
    for row in rows:
        await db.refresh(row)


# ---------------------------------------------------------------------------
# the date
# ---------------------------------------------------------------------------


def test_today_and_the_first_of_the_period_are_accepted() -> None:
    assert _create(date=today()).date == today()
    assert _create(date=current_period()).date == current_period()


@pytest.mark.parametrize(
    "date",
    [
        pytest.param(current_period() - datetime.timedelta(days=1), id="closed-period"),
        pytest.param(today() + datetime.timedelta(days=1), id="future"),
    ],
)
def test_a_date_outside_the_open_period_is_refused(date: datetime.date) -> None:
    with pytest.raises(ValidationError, match="current period"):
        _create(date=date)
    with pytest.raises(ValidationError, match="current period"):
        TransactionUpdate(date=date)
    with pytest.raises(ValidationError, match="current period"):
        BulkTransactionCreate.model_validate(_create().model_dump() | {"date": date})


def test_income_is_held_to_the_same_date_rule() -> None:
    """Income has no Expense, so the date is the only thing placing it in a Period."""
    with pytest.raises(ValidationError, match="current period"):
        _create(
            expense_id=None,
            type=TransactionType.INCOME,
            date=current_period() - datetime.timedelta(days=1),
        )


# ---------------------------------------------------------------------------
# the Expense
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("type_", [TransactionType.SAVE, TransactionType.SPEND])
async def test_no_transaction_against_an_earlier_periods_expense(
    db_session: AsyncSession, type_: TransactionType
) -> None:
    """Not only Transfers: a Save today against last month's row would raise last
    month's Allocated after the fact."""
    user, expense = await _expense(db_session)
    previous = await _previous(db_session, user, expense)

    with pytest.raises(ExpenseInClosedPeriod, match="closed period"):
        await _post(db_session, user, previous, type_, "10.00")


async def test_a_bulk_item_against_an_earlier_periods_expense_fails_the_batch(
    db_session: AsyncSession,
) -> None:
    user, expense = await _expense(db_session)
    previous = await _previous(db_session, user, expense)
    items = [
        BulkTransactionCreate.model_validate(
            _create(expense_id=e.id, type=TransactionType.SAVE).model_dump()
        )
        for e in (expense, previous)
    ]

    with pytest.raises(BulkItemFailed) as excinfo:
        await transaction_service.create_transactions(db_session, user, items)

    assert excinfo.value.index == 1
    assert excinfo.value.status_code == 422


async def test_an_edit_cannot_move_a_row_onto_an_earlier_periods_expense(
    db_session: AsyncSession,
) -> None:
    user, expense = await _expense(db_session)
    previous = await _previous(db_session, user, expense)
    (row,) = await _post(db_session, user, expense, TransactionType.SAVE, "10.00")

    with pytest.raises(ExpenseInClosedPeriod):
        await transaction_service.update_transaction(
            db_session, row, TransactionUpdate(expense_id=previous.id), user
        )


# ---------------------------------------------------------------------------
# rows recorded in an earlier Period
# ---------------------------------------------------------------------------


async def test_a_row_recorded_this_period_can_be_edited_and_deleted(
    db_session: AsyncSession,
) -> None:
    """The control: the gate is about when a row was recorded, nothing else."""
    user, expense = await _expense(db_session)
    (row,) = await _post(db_session, user, expense, TransactionType.SAVE, "10.00")

    edited = await transaction_service.update_transaction(
        db_session, row, TransactionUpdate(name="Renamed"), user
    )
    assert edited.name == "Renamed"

    await transaction_service.soft_delete_transaction(db_session, row)
    await db_session.refresh(row)
    assert row.deleted_at is not None


async def test_a_row_recorded_last_period_is_read_only(db_session: AsyncSession) -> None:
    user, expense = await _expense(db_session)
    (row,) = await _post(db_session, user, expense, TransactionType.SAVE, "10.00")
    await _recorded_last_period(db_session, row)

    with pytest.raises(RecordedInClosedPeriod) as excinfo:
        await transaction_service.update_transaction(
            db_session, row, TransactionUpdate(name="Renamed"), user
        )
    assert excinfo.value.status_code == 409

    with pytest.raises(RecordedInClosedPeriod):
        await transaction_service.soft_delete_transaction(db_session, row)
    await db_session.refresh(row)
    assert row.deleted_at is None


async def test_a_transfer_recorded_last_period_is_read_only_through_any_row(
    db_session: AsyncSession,
) -> None:
    user, expense = await _expense(db_session)
    await _fund_from_last_period(db_session, user, expense, "100.00")
    rows = await transaction_service.create_transaction(
        db_session,
        user,
        _create(expense_id=expense.id, type=TransactionType.TRANSFER),
    )
    await _recorded_last_period(db_session, *rows)

    for row in rows:
        with pytest.raises(RecordedInClosedPeriod):
            await transaction_service.update_transaction(
                db_session, row, TransactionUpdate(name="Renamed"), user
            )
        with pytest.raises(RecordedInClosedPeriod):
            await transaction_service.soft_delete_transaction(db_session, row)
