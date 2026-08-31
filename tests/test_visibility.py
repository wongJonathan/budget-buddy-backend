"""Tests for the soft-delete visibility rule and the hard-delete cascade.

Against a real Postgres, because both things under test are enforced by the database:
visibility is derived through joins rather than stored, and the cascade is a property
of the ON DELETE actions. A mocked session can express neither.

See docs/adr/0007-derived-soft-delete-visibility.md.
"""

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth_events import AuthEvents
from app.models.budget import Budget
from app.models.category import Category
from app.models.enums import Frequency, TransactionType
from app.models.expense import Expense
from app.models.transaction import Transaction
from app.models.user import User
from app.services import budget as budget_service
from app.services import expense as expense_service
from app.services import transaction as transaction_service
from app.services import user as user_service


async def seed_chain(db: AsyncSession) -> tuple[User, Budget, Expense, Transaction]:
    """One full User -> Budget -> Expense -> Transaction chain.

    Built with model constructors rather than tests.factories, because `make_expense`
    sets `monthly_cost`, which is a Postgres GENERATED column - handing it a value is
    an error against the real schema.
    """
    user = User(
        display_name="Alice",
        email=f"alice-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
    )
    db.add(user)
    await db.flush()

    budget = Budget(user_id=user.id, name="My Budget")
    db.add(budget)
    await db.flush()
    user.active_budget_id = budget.id

    category = Category(user_id=user.id, name="Groceries")
    db.add(category)
    await db.flush()

    expense = Expense(
        budget_id=budget.id,
        category_id=category.id,
        name="Milk",
        cost=Decimal("10.00"),
        frequency=Frequency.MONTHLY,
        period=datetime.date.today(),
    )
    db.add(expense)
    await db.flush()

    transaction = Transaction(
        expense_id=expense.id,
        type=TransactionType.SPEND,
        amount=Decimal("5.00"),
        date=datetime.date.today(),
    )
    db.add(transaction)
    await db.commit()

    return user, budget, expense, transaction


# ---------------------------------------------------------------------------
# visibility is derived from ancestors
# ---------------------------------------------------------------------------


async def test_a_seeded_chain_is_visible_to_start_with(db_session: AsyncSession) -> None:
    """The control: without it, every test below could pass by returning nothing."""
    await seed_chain(db_session)

    assert len(await budget_service.list_budgets(db_session)) == 1
    assert len(await expense_service.list_expenses(db_session)) == 1
    assert len(await transaction_service.list_transactions(db_session)) == 1


async def test_soft_deleting_a_budget_hides_its_expenses_and_transactions(
    db_session: AsyncSession,
) -> None:
    """The whole point of deriving visibility: one flag, two levels below it, and no
    rows rewritten."""
    _, budget, _, _ = await seed_chain(db_session)

    await budget_service.soft_delete_budget(db_session, budget.id)

    assert await budget_service.list_budgets(db_session) == []
    assert await expense_service.list_expenses(db_session) == []
    assert await transaction_service.list_transactions(db_session) == []


async def test_the_hidden_rows_are_still_there(db_session: AsyncSession) -> None:
    """Hidden, not deleted - which is what makes restore possible later."""
    _, budget, _, _ = await seed_chain(db_session)

    await budget_service.soft_delete_budget(db_session, budget.id)

    assert len((await db_session.scalars(select(Expense))).all()) == 1
    assert len((await db_session.scalars(select(Transaction))).all()) == 1


async def test_soft_deleting_an_expense_hides_its_transactions(
    db_session: AsyncSession,
) -> None:
    _, _, expense, _ = await seed_chain(db_session)

    await expense_service.soft_delete_expense(db_session, expense.id)

    assert await expense_service.list_expenses(db_session) == []
    assert await transaction_service.list_transactions(db_session) == []


async def test_soft_deleting_a_transaction_leaves_its_parents_alone(
    db_session: AsyncSession,
) -> None:
    """Visibility only ever flows downward."""
    _, _, _, transaction = await seed_chain(db_session)

    await transaction_service.soft_delete_transaction(db_session, transaction.id)

    assert await transaction_service.list_transactions(db_session) == []
    assert len(await expense_service.list_expenses(db_session)) == 1
    assert len(await budget_service.list_budgets(db_session)) == 1


# ---------------------------------------------------------------------------
# fetch-by-id obeys the same rule
# ---------------------------------------------------------------------------


async def test_a_soft_deleted_expense_is_unreachable_by_id(
    db_session: AsyncSession,
) -> None:
    """Otherwise "deleted" would only mean "hidden from browsing", and a stale link
    would still resolve."""
    _, _, expense, _ = await seed_chain(db_session)

    await expense_service.soft_delete_expense(db_session, expense.id)

    assert await expense_service.get_expense(db_session, expense.id) is None


async def test_an_expense_under_a_deleted_budget_is_unreachable_by_id(
    db_session: AsyncSession,
) -> None:
    """The Expense's own flag is untouched here - it's hidden purely by its ancestor."""
    _, budget, expense, _ = await seed_chain(db_session)

    await budget_service.soft_delete_budget(db_session, budget.id)

    assert await expense_service.get_expense(db_session, expense.id) is None
    assert await transaction_service.get_transaction(db_session, expense.id) is None


# ---------------------------------------------------------------------------
# the include_deleted escape hatch
# ---------------------------------------------------------------------------


async def test_include_deleted_surfaces_deleted_expenses_of_a_live_budget(
    db_session: AsyncSession,
) -> None:
    """Backs GET /budgets/{id}?include_deleted=true."""
    _, budget, expense, _ = await seed_chain(db_session)
    await expense_service.soft_delete_expense(db_session, expense.id)

    visible = await expense_service.list_budget_expenses(
        db_session, budget.id, datetime.date.today(), include_deleted=True
    )

    assert [e.id for e in visible] == [expense.id]


async def test_include_deleted_never_surfaces_children_of_a_deleted_budget(
    db_session: AsyncSession,
) -> None:
    """include_deleted relaxes the row's own flag, never an ancestor's - a deleted
    parent is what "deleted" means for the child."""
    _, budget, _, _ = await seed_chain(db_session)
    await budget_service.soft_delete_budget(db_session, budget.id)

    visible = await expense_service.list_budget_expenses(
        db_session, budget.id, datetime.date.today(), include_deleted=True
    )

    assert visible == []


# ---------------------------------------------------------------------------
# hard delete: the cascade, and its one documented exception
# ---------------------------------------------------------------------------


async def test_deleting_an_account_purges_everything_it_owns(
    db_session: AsyncSession,
) -> None:
    """Account deletion is now the only thing in the app that hard-deletes, so it is
    the only thing exercising the ON DELETE CASCADE chain."""
    user, _, _, _ = await seed_chain(db_session)

    await user_service.delete_user(db_session, user)

    for model in (Budget, Category, Expense, Transaction):
        assert (await db_session.scalars(select(model))).all() == [], model.__name__


async def test_deleting_an_account_keeps_the_audit_trail_intact(
    db_session: AsyncSession,
) -> None:
    """The documented exception to "purge everything": auth_events outlives the
    account, email included, so a compromised account can't erase the evidence by
    deleting itself. Only user_id is nulled."""
    user, _, _, _ = await seed_chain(db_session)
    db_session.add(
        AuthEvents(event_type="login_success", user_id=user.id, email=user.email)
    )
    await db_session.commit()

    await user_service.delete_user(db_session, user)

    (event,) = (await db_session.scalars(select(AuthEvents))).all()
    assert event.user_id is None
    assert event.email == user.email
    assert event.event_type == "login_success"
