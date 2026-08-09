import uuid
from collections.abc import Callable
from unittest.mock import MagicMock

from httpx import AsyncClient

from tests.factories import make_user


async def test_create_user(client: AsyncClient, mock_db: MagicMock) -> None:
    response = await client.post("/users", json={"display_name": "Alice"})

    assert response.status_code == 201
    body = response.json()
    assert body["display_name"] == "Alice"
    assert body["active_budget_id"] is None
    assert uuid.UUID(body["id"])
    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited_once()


async def test_list_users(
    client: AsyncClient, mock_db: MagicMock, make_scalars_result: Callable[..., MagicMock]
) -> None:
    users = [make_user(display_name="Alice"), make_user(display_name="Bob")]
    mock_db.execute.return_value = make_scalars_result(users)

    response = await client.get("/users")

    assert response.status_code == 200
    names = {u["display_name"] for u in response.json()}
    assert names == {"Alice", "Bob"}


async def test_get_user_found(client: AsyncClient, mock_db: MagicMock) -> None:
    user = make_user(display_name="Alice")
    mock_db.get.return_value = user

    response = await client.get(f"/users/{user.id}")

    assert response.status_code == 200
    assert response.json()["display_name"] == "Alice"


async def test_get_user_not_found(client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.get.return_value = None

    response = await client.get(f"/users/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_update_user_found(client: AsyncClient, mock_db: MagicMock) -> None:
    user = make_user(display_name="Alice")
    mock_db.get.return_value = user

    response = await client.patch(f"/users/{user.id}", json={"display_name": "Alicia"})

    assert response.status_code == 200
    assert response.json()["display_name"] == "Alicia"
    mock_db.commit.assert_awaited_once()


async def test_update_user_not_found(client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.get.return_value = None

    response = await client.patch(f"/users/{uuid.uuid4()}", json={"display_name": "Alicia"})

    assert response.status_code == 404


async def test_delete_user_found(client: AsyncClient, mock_db: MagicMock) -> None:
    user = make_user()
    mock_db.get.return_value = user

    response = await client.delete(f"/users/{user.id}")

    assert response.status_code == 204
    mock_db.delete.assert_awaited_once_with(user)
    mock_db.commit.assert_awaited_once()


async def test_delete_user_not_found(client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.get.return_value = None

    response = await client.delete(f"/users/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_update_last_active_found(client: AsyncClient, mock_db: MagicMock) -> None:
    user = make_user()
    mock_db.get.return_value = user

    response = await client.patch(f"/users/{user.id}/last-active")

    assert response.status_code == 200
    mock_db.commit.assert_awaited_once()


async def test_update_last_active_not_found(client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.get.return_value = None

    response = await client.patch(f"/users/{uuid.uuid4()}/last-active")

    assert response.status_code == 404
