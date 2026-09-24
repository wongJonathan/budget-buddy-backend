"""Expenses outside the open Period cannot be edited or deleted.

`CurrentPeriod` on the schema already constrains *where a row is written to*. This is
the other half - *which rows may be touched at all* - and it cannot be field validation,
because an update that simply omits `period` never reaches a validator on that field.

ADR-0010 already relies on the rule ("a late edit cannot change a shortfall Rollover has
already acted on") and ADR-0011 makes it load-bearing: deleting a historical row would
otherwise drain a Savings that later Periods still point at.

Mocked session throughout - the guard is pure Python on a row that is already loaded, so
there is nothing here for a real Postgres to decide.
"""

import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.schemas.expense import ExpenseUpdate
from app.schemas.fields import current_period
from app.services.expense import ClosedPeriod, soft_delete_expense, update_expense
from tests.factories import make_expense


def _last_month() -> datetime.date:
    """The first of the previous month - a closed Period, whatever today is."""
    return (current_period() - datetime.timedelta(days=1)).replace(day=1)


async def test_updating_a_current_period_expense_is_allowed(mock_db: MagicMock) -> None:
    """The control: without it, every test below could pass by refusing everything."""
    expense = make_expense(period=current_period(), cost=Decimal("10.00"))

    updated = await update_expense(mock_db, expense, ExpenseUpdate(cost=Decimal("25.00")))

    assert updated.cost == Decimal("25.00")
    mock_db.commit.assert_awaited_once()


async def test_updating_a_closed_period_expense_is_refused(mock_db: MagicMock) -> None:
    expense = make_expense(period=_last_month(), cost=Decimal("10.00"))

    with pytest.raises(ClosedPeriod):
        await update_expense(mock_db, expense, ExpenseUpdate(cost=Decimal("25.00")))

    assert expense.cost == Decimal("10.00")
    mock_db.commit.assert_not_awaited()


async def test_a_refused_update_is_a_409(mock_db: MagicMock) -> None:
    """Not a 404: the row exists and is the caller's. What is refused is rewriting a
    month Rollover may already have acted on."""
    expense = make_expense(period=_last_month())

    with pytest.raises(ClosedPeriod) as caught:
        await update_expense(mock_db, expense, ExpenseUpdate(name="Renamed"))

    assert caught.value.status_code == 409


async def test_deleting_a_current_period_expense_is_allowed(mock_db: MagicMock) -> None:
    expense = make_expense(period=current_period())

    await soft_delete_expense(mock_db, expense)

    assert expense.deleted_at is not None
    mock_db.commit.assert_awaited_once()


async def test_deleting_a_closed_period_expense_is_refused(mock_db: MagicMock) -> None:
    """The one ADR-0011 cares most about: this delete would drain a Savings that the
    lineage's later Periods still point at."""
    expense = make_expense(period=_last_month())

    with pytest.raises(ClosedPeriod):
        await soft_delete_expense(mock_db, expense)

    assert expense.deleted_at is None
    mock_db.commit.assert_not_awaited()


async def test_an_update_omitting_period_is_still_guarded(mock_db: MagicMock) -> None:
    """The gap this guard exists to close.

    `ExpenseUpdate.period` is `CurrentPeriod | None`, so a payload that *supplies* a
    period is already rejected by field validation. One that leaves it out was reaching
    `setattr` untouched - which is how a historical `cost` could be rewritten.
    """
    expense = make_expense(period=_last_month(), cost=Decimal("10.00"))
    data = ExpenseUpdate(cost=Decimal("999.00"))

    assert "period" not in data.model_dump(exclude_unset=True)
    with pytest.raises(ClosedPeriod):
        await update_expense(mock_db, expense, data)
