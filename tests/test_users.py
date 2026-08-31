"""Tests for app/routers/users.py.

Every route here is authenticated and acts on the *current* user. None of them takes
a user id in a position the handler reads, so a request can only ever affect the
account it is authenticated as - there is no "delete someone else" shape to test for.

Each route therefore gets two tests: the authenticated behaviour via `authed_client`,
and the 401 via the plain `client`, which presents no session.
"""

import datetime
import uuid
from collections.abc import Callable
from unittest.mock import MagicMock

from httpx import AsyncClient

from app.models.user import User
from tests.factories import make_budget

# ---------------------------------------------------------------------------
# GET /users
# ---------------------------------------------------------------------------


async def test_get_user_returns_the_current_user_with_their_budgets(
    authed_client: AsyncClient,
    mock_db: MagicMock,
    current_user: User,
    make_scalars_result: Callable[..., MagicMock],
) -> None:
    mock_db.execute.return_value = make_scalars_result(
        [make_budget(user_id=current_user.id, name="Groceries")]
    )

    response = await authed_client.get("/users")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(current_user.id)
    assert body["display_name"] == "Alice"
    assert [budget["name"] for budget in body["budgets"]] == ["Groceries"]


async def test_get_user_with_no_budgets_returns_an_empty_list(
    authed_client: AsyncClient,
) -> None:
    """A freshly provisioned account has one Budget, but a user who deleted it has
    none - and that has to serialise rather than 500."""
    response = await authed_client.get("/users")

    assert response.status_code == 200
    assert response.json()["budgets"] == []


async def test_get_user_requires_a_session(client: AsyncClient) -> None:
    response = await client.get("/users")

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /users
# ---------------------------------------------------------------------------


async def test_update_user_changes_the_current_user(
    authed_client: AsyncClient, mock_db: MagicMock, current_user: User
) -> None:
    response = await authed_client.patch("/users", json={"display_name": "Alicia"})

    assert response.status_code == 200
    assert response.json()["display_name"] == "Alicia"
    assert current_user.display_name == "Alicia"
    mock_db.commit.assert_awaited_once()


async def test_update_user_ignores_fields_that_were_not_sent(
    authed_client: AsyncClient, current_user: User
) -> None:
    """`exclude_unset` - an omitted field must stay as it was, not become None."""
    current_user.active_budget_id = uuid.uuid4()
    original = current_user.active_budget_id

    await authed_client.patch("/users", json={"display_name": "Alicia"})

    assert current_user.active_budget_id == original


async def test_update_user_requires_a_session(client: AsyncClient) -> None:
    response = await client.patch("/users", json={"display_name": "Alicia"})

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /users
# ---------------------------------------------------------------------------


async def test_delete_user_erases_the_current_account(
    authed_client: AsyncClient, mock_db: MagicMock, current_user: User
) -> None:
    response = await authed_client.delete("/users")

    assert response.status_code == 204
    mock_db.delete.assert_awaited_once_with(current_user)
    mock_db.commit.assert_awaited_once()


async def test_delete_user_clears_budgets_then_categories_first(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    """The order is forced by the schema, not a preference: expenses.category_id is
    ON DELETE RESTRICT and checked immediately, so deleting the User outright is
    refused while their Expenses still reference their Categories. Budgets go first
    (taking Expenses and Transactions), which frees the Categories. See ADR 0007.
    """
    await authed_client.delete("/users")

    deleted_tables = [str(call.args[0].table) for call in mock_db.execute.call_args_list]
    assert deleted_tables == ["budgets", "categories"]


async def test_delete_user_requires_a_session(client: AsyncClient) -> None:
    response = await client.delete("/users")

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /users/{user_id}/last-active
# ---------------------------------------------------------------------------


async def test_update_last_active_touches_the_current_user(
    authed_client: AsyncClient, mock_db: MagicMock, current_user: User
) -> None:
    current_user.last_active = datetime.date(2020, 1, 1)

    response = await authed_client.patch(f"/users/{current_user.id}/last-active")

    assert response.status_code == 200
    assert current_user.last_active == datetime.date.today()
    mock_db.commit.assert_awaited_once()


async def test_update_last_active_requires_a_session(client: AsyncClient) -> None:
    response = await client.patch(f"/users/{uuid.uuid4()}/last-active")

    assert response.status_code == 401
