"""Service-level tests for the Transaction listing.

Against a real Postgres, because what is under test is a WHERE, an ORDER BY and a
LIMIT/OFFSET. A mocked session hands back whatever the test stubbed, so it can confirm
the router unpacks a page but never that the query selected the right rows, in the right
order, without overlapping the page before it.
"""

import datetime
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import TransactionType
from app.models.transaction import Transaction
from app.models.user import User
from app.services import transaction as transaction_service
from app.services.transaction import InvalidDateRange

MARCH = datetime.date(2027, 3, 1)
MARCH_END = datetime.date(2027, 3, 31)


async def _user(db: AsyncSession) -> User:
    user = User(
        display_name="Alice",
        email=f"alice-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
    )
    db.add(user)
    await db.flush()
    return user


async def _income(db: AsyncSession, user: User, day: datetime.date, amount: str) -> None:
    """Income, so no Budget/Category/Expense chain is needed to place a row on a date.

    A Transaction with no Expense is a first-class row here, not a special case - see
    docs/adr/0009.
    """
    db.add(
        Transaction(
            user_id=user.id,
            type=TransactionType.INCOME,
            name=f"pay {day.isoformat()}",
            amount=Decimal(amount),
            date=day,
        )
    )
    await db.flush()


async def _page(
    db: AsyncSession,
    user: User,
    *,
    date_from: datetime.date = MARCH,
    date_to: datetime.date = MARCH_END,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Transaction], int]:
    rows, total = await transaction_service.list_transactions(
        db, user, date_from=date_from, date_to=date_to, limit=limit, offset=offset
    )
    return list(rows), total


async def test_the_window_is_inclusive_at_both_ends(db_session: AsyncSession) -> None:
    """A row on the boundary is in the window, at either end.

    Half-open would silently drop every transaction dated the last of the month, which
    is where a salary or a rent payment most often lands.
    """
    user = await _user(db_session)
    await _income(db_session, user, MARCH, "1.00")
    await _income(db_session, user, MARCH_END, "2.00")

    rows, total = await _page(db_session, user)

    assert {row.date for row in rows} == {MARCH, MARCH_END}
    assert total == 2


async def test_rows_outside_the_window_are_excluded(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    await _income(db_session, user, datetime.date(2027, 2, 28), "1.00")
    await _income(db_session, user, MARCH, "2.00")
    await _income(db_session, user, datetime.date(2027, 4, 1), "3.00")

    rows, total = await _page(db_session, user)

    assert [row.date for row in rows] == [MARCH]
    assert total == 1


async def test_the_page_is_newest_first(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    for day in (5, 20, 12):
        await _income(db_session, user, MARCH.replace(day=day), "1.00")

    rows, _ = await _page(db_session, user)

    assert [row.date.day for row in rows] == [20, 12, 5]


async def test_paging_partitions_the_window_without_gaps_or_repeats(
    db_session: AsyncSession,
) -> None:
    """Every row appears on exactly one page.

    All five share a date, which is the case a `date`-only ORDER BY cannot answer
    stably: the planner may order ties differently for offset 0 and offset 2, so a row
    can land on both pages or on neither. The tie-break on `id` is what forbids it.
    """
    user = await _user(db_session)
    for amount in ("1.00", "2.00", "3.00", "4.00", "5.00"):
        await _income(db_session, user, MARCH, amount)

    first, total = await _page(db_session, user, limit=2, offset=0)
    second, _ = await _page(db_session, user, limit=2, offset=2)
    third, _ = await _page(db_session, user, limit=2, offset=4)

    assert total == 5
    assert [len(first), len(second), len(third)] == [2, 2, 1]
    seen = [row.id for row in first + second + third]
    assert len(set(seen)) == 5


async def test_total_counts_the_window_not_the_page(db_session: AsyncSession) -> None:
    """Otherwise a client cannot size a pager without walking to the end."""
    user = await _user(db_session)
    for day in range(1, 11):
        await _income(db_session, user, MARCH.replace(day=day), "1.00")

    rows, total = await _page(db_session, user, limit=3)

    assert len(rows) == 3
    assert total == 10


async def test_another_users_transactions_are_never_in_the_page(
    db_session: AsyncSession,
) -> None:
    alice = await _user(db_session)
    bob = await _user(db_session)
    await _income(db_session, alice, MARCH, "1.00")
    await _income(db_session, bob, MARCH, "2.00")

    rows, total = await _page(db_session, alice)

    assert total == 1
    assert [row.user_id for row in rows] == [alice.id]


async def test_a_deleted_transaction_is_absent_and_uncounted(db_session: AsyncSession) -> None:
    """`total` has to agree with the page about what is visible."""
    user = await _user(db_session)
    await _income(db_session, user, MARCH, "1.00")
    await _income(db_session, user, MARCH.replace(day=2), "2.00")
    rows, _ = await _page(db_session, user)
    await transaction_service.soft_delete_transaction(db_session, rows[0])

    remaining, total = await _page(db_session, user)

    assert total == 1
    assert len(remaining) == 1


async def test_a_backwards_window_is_refused(db_session: AsyncSession) -> None:
    """Before the query runs, not as an empty page that looks like 'nothing here'."""
    user = await _user(db_session)

    with pytest.raises(InvalidDateRange, match="is after date_to"):
        await _page(db_session, user, date_from=MARCH_END, date_to=MARCH)


async def test_an_empty_window_is_an_empty_page_not_an_error(db_session: AsyncSession) -> None:
    """A well-formed window with nothing in it is a legitimate answer."""
    user = await _user(db_session)
    await _income(db_session, user, MARCH, "1.00")

    rows, total = await _page(
        db_session, user, date_from=datetime.date(2027, 6, 1), date_to=datetime.date(2027, 6, 30)
    )

    assert rows == []
    assert total == 0


# ---------------------------------------------------------------------------
# the ordering contract: date, then created_at, then id (docs/adr/0013)
# ---------------------------------------------------------------------------


async def test_created_at_breaks_a_tie_between_rows_on_the_same_date(
    db_session: AsyncSession,
) -> None:
    """Two purchases the same day come back newest-entered first.

    Each is committed separately so they get distinct `created_at` values - `now()` is
    transaction-start time, so rows sharing a transaction share it. `id` cannot answer
    this: it is a v4 UUID, stable but shuffled, which is the whole reason this column
    exists."""
    user = await _user(db_session)
    await _income(db_session, user, MARCH, "1.00")
    await db_session.commit()
    await _income(db_session, user, MARCH, "2.00")
    await db_session.commit()

    rows, _ = await _page(db_session, user)

    assert [row.amount for row in rows] == [Decimal("2.00"), Decimal("1.00")]
    assert rows[0].created_at > rows[1].created_at


async def test_rows_sharing_a_date_and_a_created_at_still_order_repeatably(
    db_session: AsyncSession,
) -> None:
    """The case `id` is still in the sort for.

    Written in one transaction, so `now()` gives them an identical `created_at` - the
    shape the spend split produces, where a SPEND_SAVED and its SPEND come from one
    payload and one commit. Neither of the first two keys separates them, so the order
    is arbitrary; what it must not be is *different* between two runs of the same
    query, which is what would let a row show up on two pages."""
    user = await _user(db_session)
    for amount in ("1.00", "2.00", "3.00", "4.00"):
        await _income(db_session, user, MARCH, amount)
    await db_session.commit()

    first_run, _ = await _page(db_session, user)
    second_run, _ = await _page(db_session, user)

    assert len({row.created_at for row in first_run}) == 1
    assert [row.id for row in first_run] == [row.id for row in second_run]


async def test_paging_across_an_identical_created_at_repeats_nothing(
    db_session: AsyncSession,
) -> None:
    """And the repeatable order has to hold across a page boundary, not just within one."""
    user = await _user(db_session)
    for amount in ("1.00", "2.00", "3.00", "4.00"):
        await _income(db_session, user, MARCH, amount)
    await db_session.commit()

    first, total = await _page(db_session, user, limit=2, offset=0)
    second, _ = await _page(db_session, user, limit=2, offset=2)

    assert total == 4
    assert len({row.id for row in first + second}) == 4


async def test_date_outranks_created_at(db_session: AsyncSession) -> None:
    """A backdated row entered last sorts by its date, not by its arrival.

    This is what keeps the listing a statement about the User's money rather than about
    the write log - and `date` is editable, so an ordering led by `created_at` would
    make correcting a date appear to do nothing."""
    user = await _user(db_session)
    await _income(db_session, user, MARCH.replace(day=20), "1.00")
    await db_session.commit()
    await _income(db_session, user, MARCH.replace(day=5), "2.00")
    await db_session.commit()

    rows, _ = await _page(db_session, user)

    # Entered second, dated earlier - so it comes last despite the newer created_at.
    assert [row.date.day for row in rows] == [20, 5]
    assert rows[1].created_at > rows[0].created_at
