"""A Transaction's Category is its Expense's, read live - never stored on the row.

Against a real Postgres, because `Transaction.category_id` is a correlated subquery: a
mocked session would hand back whatever the test stubbed and prove nothing about the
join. Reads after a write go through a fresh session, the way the next request would,
so the identity map can't serve a value loaded before the change.
"""

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.category import Category
from app.models.enums import TransactionType
from app.models.expense import Expense
from app.models.transaction import Transaction
from app.models.user import User
from app.services import transaction as transaction_service
from app.services.visibility import live_transactions
from tests.services.test_savings import _expense, _post

WINDOW = (datetime.date.today().replace(day=1), datetime.date.today())


async def _other_category(db: AsyncSession, user: User) -> Category:
    category = Category(user_id=user.id, name="Household")
    db.add(category)
    await db.commit()
    return category


async def _listed(
    sessionmaker: async_sessionmaker[AsyncSession], user: User, category_id: uuid.UUID | None = None
) -> list[Transaction]:
    async with sessionmaker() as db:
        rows, _ = await transaction_service.list_transactions(
            db,
            user,
            date_from=WINDOW[0],
            date_to=WINDOW[1],
            limit=200,
            offset=0,
            category_id=category_id,
        )
        return list(rows)


async def test_every_row_a_write_produces_carries_the_expense_category(
    db_session: AsyncSession,
) -> None:
    """Including both halves of a split spend, which a set-on-create path once missed."""
    user, expense = await _expense(db_session)
    saved = await _post(db_session, user, expense, TransactionType.SAVE, "50.00")
    split = await _post(db_session, user, expense, TransactionType.SPEND, "80.00")

    assert {t.type for t in split} == {TransactionType.SPEND_SAVED, TransactionType.SPEND}
    assert {t.category_id for t in [*saved, *split]} == {expense.category_id}


async def test_income_has_no_category(db_session: AsyncSession) -> None:
    user, _ = await _expense(db_session)
    [income] = await _post(db_session, user, None, TransactionType.INCOME, "1000.00")

    assert income.category_id is None


async def test_recategorizing_the_expense_moves_its_transactions(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SPEND, "40.00")
    household = await _other_category(db_session, user)

    expense.category_id = household.id
    await db_session.commit()

    [spend] = await _listed(db_sessionmaker, user)
    assert spend.category_id == household.id


async def test_the_list_filters_by_category(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    user, groceries = await _expense(db_session)
    household = await _other_category(db_session, user)
    rent = Expense(
        budget_id=groceries.budget_id,
        category_id=household.id,
        user_id=user.id,
        name="Rent",
        cost=Decimal("900.00"),
        frequency=groceries.frequency,
        period=groceries.period,
    )
    db_session.add(rent)
    await db_session.commit()

    await _post(db_session, user, groceries, TransactionType.SPEND, "40.00")
    await _post(db_session, user, rent, TransactionType.SPEND, "900.00")
    await _post(db_session, user, None, TransactionType.INCOME, "2000.00")

    rows = await _listed(db_sessionmaker, user, category_id=household.id)

    assert [t.expense_id for t in rows] == [rent.id]


async def test_an_unknown_category_is_an_empty_page_not_an_error(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """A foreign id reads the same as an unknown one: nothing of this user's matches."""
    user, expense = await _expense(db_session)
    _, other_expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SPEND, "40.00")

    assert await _listed(db_sessionmaker, user, category_id=other_expense.category_id) == []


async def test_a_deleted_expense_still_labels_its_transactions(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The category is a label, not a count - the Expense's deletion doesn't strip it."""
    user, expense = await _expense(db_session)
    await _post(db_session, user, expense, TransactionType.SPEND, "40.00")
    expense.deleted_at = func.now()
    await db_session.commit()

    async with db_sessionmaker() as db:
        [spend] = (
            await db.scalars(
                live_transactions(include_deleted=True).where(Transaction.user_id == user.id)
            )
        ).all()

    assert spend.category_id == expense.category_id
