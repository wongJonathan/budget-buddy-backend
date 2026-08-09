import uuid
from collections.abc import Callable
from unittest.mock import MagicMock

from httpx import AsyncClient

from tests.factories import make_category


async def test_create_category(client: AsyncClient, mock_db: MagicMock) -> None:
    user_id = uuid.uuid4()
    response = await client.post("/categories", json={"user_id": str(user_id), "name": "Groceries"})

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Groceries"
    assert body["user_id"] == str(user_id)
    assert body["system_type"] is None
    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited_once()


async def test_create_category_with_system_type(client: AsyncClient, mock_db: MagicMock) -> None:
    response = await client.post(
        "/categories",
        json={"user_id": str(uuid.uuid4()), "name": "Income", "system_type": "income"},
    )

    assert response.status_code == 201
    assert response.json()["system_type"] == "income"


async def test_list_categories(
    client: AsyncClient, mock_db: MagicMock, make_scalars_result: Callable[..., MagicMock]
) -> None:
    categories = [make_category(name="Groceries"), make_category(name="Rent")]
    mock_db.execute.return_value = make_scalars_result(categories)

    response = await client.get("/categories")

    assert response.status_code == 200
    names = {c["name"] for c in response.json()}
    assert names == {"Groceries", "Rent"}


async def test_get_category_found(client: AsyncClient, mock_db: MagicMock) -> None:
    category = make_category(name="Groceries")
    mock_db.get.return_value = category

    response = await client.get(f"/categories/{category.id}")

    assert response.status_code == 200
    assert response.json()["name"] == "Groceries"


async def test_get_category_not_found(client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.get.return_value = None

    response = await client.get(f"/categories/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_update_category_found(client: AsyncClient, mock_db: MagicMock) -> None:
    category = make_category(name="Groceries")
    mock_db.get.return_value = category

    response = await client.patch(f"/categories/{category.id}", json={"name": "Food"})

    assert response.status_code == 200
    assert response.json()["name"] == "Food"
    mock_db.commit.assert_awaited_once()


async def test_update_category_not_found(client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.get.return_value = None

    response = await client.patch(f"/categories/{uuid.uuid4()}", json={"name": "Food"})

    assert response.status_code == 404


async def test_delete_category_found(client: AsyncClient, mock_db: MagicMock) -> None:
    category = make_category()
    mock_db.get.return_value = category

    response = await client.delete(f"/categories/{category.id}")

    assert response.status_code == 204
    mock_db.delete.assert_awaited_once_with(category)
    mock_db.commit.assert_awaited_once()


async def test_delete_category_not_found(client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.get.return_value = None

    response = await client.delete(f"/categories/{uuid.uuid4()}")

    assert response.status_code == 404
