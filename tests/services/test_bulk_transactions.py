"""Bulk create: one DB transaction, items applied in order, no Transfers.

Against a real Postgres: whether a later item sees an earlier one depends on the fund
balance, a sum over flushed rows that a mocked session cannot compute.
"""

import datetime
import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import TransactionType
from app.models.expense import Expense
from app.schemas.transaction import BulkTransactionCreate
from app.services import transaction as transaction_service
from app.services.transaction import BulkItemFailed
from tests.services.test_savings import _expense, _transactions


def _item(
    expense: Expense | None, type_: TransactionType, amount: str, **overrides: object
) -> BulkTransactionCreate:
    fields: dict[str, object] = {
        "expense_id": expense.id if expense else None,
        "type": type_,
        "name": f"{type_.value} {amount}",
        "amount": Decimal(amount),
        "date": datetime.date.today(),
    }
    fields.update(overrides)
    return BulkTransactionCreate.model_validate(fields)


async def test_every_item_is_written_and_returned(db_session: AsyncSession) -> None:
    user, expense = await _expense(db_session)

    rows = await transaction_service.create_transactions(
        db_session,
        user,
        [
            _item(None, TransactionType.INCOME, "1000.00"),
            _item(expense, TransactionType.SPEND, "40.00"),
        ],
    )

    assert [(r.type, r.amount) for r in rows] == [
        (TransactionType.INCOME, Decimal("1000.00")),
        (TransactionType.SPEND, Decimal("40.00")),
    ]
    assert len(await _transactions(db_session, user)) == 2


async def test_a_later_item_sees_the_fund_an_earlier_one_filled(
    db_session: AsyncSession,
) -> None:
    """Items apply in order: a Spend after a Save in the same batch draws on it."""
    user, expense = await _expense(db_session)

    rows = await transaction_service.create_transactions(
        db_session,
        user,
        [
            _item(expense, TransactionType.SAVE, "30.00"),
            _item(expense, TransactionType.SPEND, "50.00"),
        ],
    )

    assert sorted((r.type, r.amount) for r in rows) == sorted(
        [
            (TransactionType.SAVE, Decimal("30.00")),
            (TransactionType.SPEND_SAVED, Decimal("30.00")),
            (TransactionType.SPEND, Decimal("20.00")),
        ]
    )


async def test_a_failing_item_writes_nothing_and_is_named(
    db_session: AsyncSession,
) -> None:
    user, expense = await _expense(db_session)

    with pytest.raises(BulkItemFailed) as failed:
        await transaction_service.create_transactions(
            db_session,
            user,
            [
                _item(None, TransactionType.INCOME, "1000.00"),
                _item(expense, TransactionType.SAVE, "30.00"),
                _item(None, TransactionType.SPEND, "5.00", expense_id=uuid.uuid4()),
            ],
        )

    assert failed.value.index == 2
    assert failed.value.status_code == 404
    # The rollback expired every loaded row.
    await db_session.refresh(user)
    await db_session.refresh(expense)
    assert await _transactions(db_session, user) == []
    # The Save's fund was opened and stamped on the Expense before the failure.
    assert expense.savings_id is None


def test_a_transfer_is_refused() -> None:
    with pytest.raises(ValidationError, match="transfer"):
        _item(None, TransactionType.TRANSFER, "10.00", expense_id=uuid.uuid4())
