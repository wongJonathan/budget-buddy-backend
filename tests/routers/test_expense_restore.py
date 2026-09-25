"""`POST /expenses/{expense_id}/restore`, end to end.

Real Postgres behind the real router, because the route's job is mostly its path
dependency: it has to reach a *deleted* row (the readable lookup), still 404 on another
user's row or one under a deleted Budget, and serialise the result as `ExpenseRead`. The
money side of Restore is covered in tests/services/test_restore_expense.py.
"""

import datetime
import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.main import app
from app.models.budget import Budget
from app.models.category import Category
from app.models.enums import Frequency
from app.models.expense import Expense
from app.models.user import User
from app.schemas.fields import current_period


async def _user(db: AsyncSession, name: str) -> User:
    user = User(
        display_name=name,
        email=f"{name.lower()}-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
    )
    db.add(user)
    await db.commit()
    return user


@pytest.fixture
async def alice(db_session: AsyncSession) -> User:
    return await _user(db_session, "Alice")


@pytest.fixture
async def db_client(db_session: AsyncSession, alice: User) -> AsyncGenerator[AsyncClient]:
    """A client authenticated as `alice`, talking to the real test database."""

    async def _override_get_session() -> AsyncGenerator[AsyncSession]:
        yield db_session

    async def _override_current_user() -> User:
        return alice

    app.dependency_overrides[get_db_session] = _override_get_session
    app.dependency_overrides[get_current_user] = _override_current_user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _expense(db: AsyncSession, user: User, *, period: datetime.date | None = None) -> Expense:
    budget = Budget(user_id=user.id, name="Budget")
    category = Category(user_id=user.id, name="Food")
    db.add_all([budget, category])
    await db.flush()
    expense = Expense(
        budget_id=budget.id,
        category_id=category.id,
        user_id=user.id,
        name="Groceries",
        cost=Decimal("200.00"),
        frequency=Frequency.MONTHLY,
        period=period or current_period(),
    )
    db.add(expense)
    await db.commit()
    return expense


async def test_restore_returns_the_restored_expense(
    db_session: AsyncSession, db_client: AsyncClient, alice: User
) -> None:
    expense = await _expense(db_session, alice)
    assert (await db_client.delete(f"/expenses/{expense.id}")).status_code == 204

    response = await db_client.post(f"/expenses/{expense.id}/restore")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(expense.id)
    assert body["deleted_at"] is None


async def test_a_restored_expense_is_writable_again(
    db_session: AsyncSession, db_client: AsyncClient, alice: User
) -> None:
    expense = await _expense(db_session, alice)
    await db_client.delete(f"/expenses/{expense.id}")
    await db_client.post(f"/expenses/{expense.id}/restore")

    response = await db_client.patch(f"/expenses/{expense.id}", json={"name": "Food"})

    assert response.status_code == 200, response.text


async def test_restore_is_post_only(
    db_session: AsyncSession, db_client: AsyncClient, alice: User
) -> None:
    expense = await _expense(db_session, alice)
    await db_client.delete(f"/expenses/{expense.id}")

    response = await db_client.patch(f"/expenses/{expense.id}/restore")

    assert response.status_code == 405


async def test_restoring_a_live_expense_is_409(
    db_session: AsyncSession, db_client: AsyncClient, alice: User
) -> None:
    expense = await _expense(db_session, alice)

    response = await db_client.post(f"/expenses/{expense.id}/restore")

    assert response.status_code == 409


async def test_restoring_twice_is_409(
    db_session: AsyncSession, db_client: AsyncClient, alice: User
) -> None:
    expense = await _expense(db_session, alice)
    await db_client.delete(f"/expenses/{expense.id}")
    assert (await db_client.post(f"/expenses/{expense.id}/restore")).status_code == 200

    response = await db_client.post(f"/expenses/{expense.id}/restore")

    assert response.status_code == 409


async def test_restoring_from_a_closed_period_is_409(
    db_session: AsyncSession, db_client: AsyncClient, alice: User
) -> None:
    last_month = (current_period() - datetime.timedelta(days=1)).replace(day=1)
    expense = await _expense(db_session, alice, period=last_month)
    expense.deleted_at = func.now()
    await db_session.commit()

    response = await db_client.post(f"/expenses/{expense.id}/restore")

    assert response.status_code == 409
    assert "closed period" in response.json()["detail"]


async def test_restoring_another_users_expense_is_404(
    db_session: AsyncSession, db_client: AsyncClient
) -> None:
    bob = await _user(db_session, "Bob")
    expense = await _expense(db_session, bob)
    expense.deleted_at = func.now()
    await db_session.commit()

    response = await db_client.post(f"/expenses/{expense.id}/restore")

    assert response.status_code == 404


async def test_restoring_under_a_deleted_budget_is_404(
    db_session: AsyncSession, db_client: AsyncClient, alice: User
) -> None:
    """A deleted Budget hides its Expenses entirely (the ancestor rule), so there is
    nothing to restore into."""
    expense = await _expense(db_session, alice)
    await db_client.delete(f"/expenses/{expense.id}")
    budget = await db_session.get(Budget, expense.budget_id)
    assert budget is not None
    budget.deleted_at = func.now()
    await db_session.commit()

    response = await db_client.post(f"/expenses/{expense.id}/restore")

    assert response.status_code == 404
