"""Transactions against an Expense that has been deleted, end to end.

Real Postgres behind the real routers, because what is under test is the whole path:
the path dependencies, the payload walk in `verify_owned_refs`, and what deleting an
Expense does to the rows beneath it. The mocked session can't show any of that - it
hands back whatever row the test stubbed, deleted or not.
"""

import datetime
import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.main import app
from app.models.budget import Budget
from app.models.category import Category
from app.models.user import User
from app.schemas.fields import current_period


@pytest.fixture
async def alice(db_session: AsyncSession) -> User:
    user = User(
        display_name="Alice",
        email=f"alice-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest.fixture
async def db_client(
    db_session: AsyncSession, alice: User
) -> AsyncGenerator[AsyncClient]:
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


async def _create_expense(db: AsyncSession, client: AsyncClient, user: User) -> str:
    budget = Budget(user_id=user.id, name="Budget")
    category = Category(user_id=user.id, name="Food")
    db.add_all([budget, category])
    await db.commit()

    response = await client.post(
        "/expenses",
        json={
            "budget_id": str(budget.id),
            "category_id": str(category.id),
            "name": "Groceries",
            "cost": "200.00",
            "frequency": "monthly",
            "period": current_period().isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    expense_id: str = response.json()["id"]
    return expense_id


def _spend(expense_id: str) -> dict[str, str]:
    return {
        "expense_id": expense_id,
        "type": "spend",
        "name": "Shop",
        "amount": "25.00",
        "date": datetime.date.today().isoformat(),
    }


async def test_creating_a_transaction_for_a_deleted_expense_is_refused(
    db_session: AsyncSession, db_client: AsyncClient, alice: User
) -> None:
    """The Expense is the caller's, so it's a 409 rather than a 404: ownership is
    established and there is nothing to hide. A deleted row is never a valid parent."""
    expense_id = await _create_expense(db_session, db_client, alice)
    assert (await db_client.delete(f"/expenses/{expense_id}")).status_code == 204

    response = await db_client.post("/transactions", json=_spend(expense_id))

    assert response.status_code == 409
    assert response.json()["detail"] == "Expense is deleted"


async def test_a_transaction_becomes_read_only_once_its_expense_is_deleted(
    db_session: AsyncSession, db_client: AsyncClient, alice: User
) -> None:
    """Deleting an Expense withdraws its Period's Transactions (docs/adr/0014), and a
    withdrawn Transaction is read-only until the Expense is Restored."""
    expense_id = await _create_expense(db_session, db_client, alice)
    created = await db_client.post("/transactions", json=_spend(expense_id))
    assert created.status_code == 201, created.text
    (transaction,) = created.json()
    transaction_url = f"/transactions/{transaction['id']}"

    before = await db_client.patch(transaction_url, json={"amount": "30.00"})
    assert before.status_code == 200, before.text

    assert (await db_client.delete(f"/expenses/{expense_id}")).status_code == 204

    after = await db_client.patch(transaction_url, json={"amount": "35.00"})
    assert after.status_code == 409
    assert after.json()["detail"] == "Transaction is deleted"
