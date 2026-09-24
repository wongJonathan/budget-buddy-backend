"""Tests for app/routers/categories.py.

Every route sits behind `CurrentUser`, and the ones taking a `{category_id}`
resolve it through `OwnedCategories`, so a category belonging to someone else is
a 404 rather than a 403. Fetch-by-id therefore goes through `db.scalars`, not
`db.get` - the ownership lookup is a filtered select.

What a mocked session can show here is that the routes are wired to the right
dependency and that ownership comes from the session rather than the payload.
That the filter actually excludes another user's row is asserted against real
Postgres in test_ownership.py; `mock_db.scalars` returns whatever it was handed.
"""

import uuid
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient

from app.models.user import User
from tests.factories import make_category, make_scalars_one


async def test_create_category(
    authed_client: AsyncClient, mock_db: MagicMock, current_user: User
) -> None:
    response = await authed_client.post("/categories", json={"name": "Groceries"})

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Groceries"
    # Ownership comes from the session - CategoryCreate has no user_id to forge.
    assert body["user_id"] == str(current_user.id)
    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited_once()


async def test_a_system_type_in_the_payload_cannot_reintroduce_reserved_behaviour(
    authed_client: AsyncClient,
) -> None:
    """`system_type` is gone from the schema and the table (docs/adr/0009).

    A client still sending the old field gets a plain Category, not a reserved one -
    the extra key is dropped, and nothing in the response carries it back.
    """
    response = await authed_client.post(
        "/categories", json={"name": "Income", "system_type": "income"}
    )

    assert response.status_code == 201
    assert "system_type" not in response.json()


async def test_create_category_ignores_a_user_id_in_the_payload(
    authed_client: AsyncClient, current_user: User
) -> None:
    """The field is gone from the schema, so sending one cannot reassign the owner."""
    response = await authed_client.post(
        "/categories", json={"user_id": str(uuid.uuid4()), "name": "Groceries"}
    )

    assert response.status_code == 201
    assert response.json()["user_id"] == str(current_user.id)


async def test_get_category_found(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    category = make_category(name="Groceries")
    mock_db.scalars.return_value = make_scalars_one(category)

    response = await authed_client.get(f"/categories/{category.id}")

    assert response.status_code == 200
    assert response.json()["name"] == "Groceries"


async def test_get_category_not_found(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    """Also the shape of "it's someone else's" - the lookup filters on user_id, so
    both cases come back as no row at all."""
    mock_db.scalars.return_value = make_scalars_one(None)

    response = await authed_client.get(f"/categories/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Category not found"


async def test_update_category_found(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    category = make_category(name="Groceries")
    mock_db.scalars.return_value = make_scalars_one(category)

    response = await authed_client.patch(f"/categories/{category.id}", json={"name": "Food"})

    assert response.status_code == 200
    assert response.json()["name"] == "Food"
    mock_db.commit.assert_awaited_once()


async def test_update_category_not_found(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.scalars.return_value = make_scalars_one(None)

    response = await authed_client.patch(f"/categories/{uuid.uuid4()}", json={"name": "Food"})

    assert response.status_code == 404


async def test_delete_category_is_soft_delete(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    category = make_category()
    mock_db.scalars.return_value = make_scalars_one(category)

    response = await authed_client.delete(f"/categories/{category.id}")

    assert response.status_code == 204
    assert category.deleted_at is not None
    mock_db.delete.assert_not_called()
    mock_db.commit.assert_awaited_once()


async def test_delete_category_not_found(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.scalars.return_value = make_scalars_one(None)

    response = await authed_client.delete(f"/categories/{uuid.uuid4()}")

    assert response.status_code == 404
    mock_db.delete.assert_not_called()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/categories"),
        ("GET", "/categories/{category_id}"),
        ("PATCH", "/categories/{category_id}"),
        ("DELETE", "/categories/{category_id}"),
    ],
)
async def test_category_routes_require_a_session(
    client: AsyncClient, mock_db: MagicMock, method: str, path: str
) -> None:
    """`mock_db` is stubbed to find a category, so a 401 can only come from the
    auth dependency and not from the row being missing.
    """
    mock_db.scalars.return_value = make_scalars_one(make_category())

    response = await client.request(
        method, path.format(category_id=uuid.uuid4()), json={"name": "Groceries"}
    )

    assert response.status_code == 401
