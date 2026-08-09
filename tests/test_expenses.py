import uuid
from collections.abc import Callable
from decimal import Decimal
from unittest.mock import MagicMock

from httpx import AsyncClient

from tests.factories import make_expense


def _expense_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "budget_id": str(uuid.uuid4()),
        "category_id": str(uuid.uuid4()),
        "name": "Groceries",
        "cost": "100.00",
        "frequency": "monthly",
        "period": "2026-08-01",
    }
    payload.update(overrides)
    return payload


async def test_create_expense(client: AsyncClient, mock_db: MagicMock) -> None:
    response = await client.post("/expenses", json=_expense_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Groceries"
    assert body["frequency"] == "monthly"
    assert Decimal(body["cost"]) == Decimal("100.00")
    assert Decimal(body["amount_saved"]) == Decimal("0")
    assert uuid.UUID(body["series_id"])
    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited_once()


async def test_create_expense_optional_fields_default(
    client: AsyncClient, mock_db: MagicMock
) -> None:
    response = await client.post("/expenses", json=_expense_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["note"] is None
    assert body["goal_amount"] is None
    assert body["goal_date"] is None
    assert body["is_deactivated"] is False


async def test_list_expenses(
    client: AsyncClient, mock_db: MagicMock, make_scalars_result: Callable[..., MagicMock]
) -> None:
    expenses = [make_expense(name="Groceries"), make_expense(name="Rent")]
    mock_db.execute.return_value = make_scalars_result(expenses)

    response = await client.get("/expenses")

    assert response.status_code == 200
    names = {e["name"] for e in response.json()}
    assert names == {"Groceries", "Rent"}


async def test_get_expense_found(client: AsyncClient, mock_db: MagicMock) -> None:
    expense = make_expense(name="Groceries")
    mock_db.get.return_value = expense

    response = await client.get(f"/expenses/{expense.id}")

    assert response.status_code == 200
    assert response.json()["name"] == "Groceries"


async def test_get_expense_not_found(client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.get.return_value = None

    response = await client.get(f"/expenses/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_update_expense_found(client: AsyncClient, mock_db: MagicMock) -> None:
    expense = make_expense(name="Groceries")
    mock_db.get.return_value = expense

    response = await client.patch(f"/expenses/{expense.id}", json={"name": "Food"})

    assert response.status_code == 200
    assert response.json()["name"] == "Food"
    mock_db.commit.assert_awaited_once()


async def test_update_expense_not_found(client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.get.return_value = None

    response = await client.patch(f"/expenses/{uuid.uuid4()}", json={"name": "Food"})

    assert response.status_code == 404


async def test_delete_expense_is_soft_delete(client: AsyncClient, mock_db: MagicMock) -> None:
    expense = make_expense(is_deactivated=False)
    mock_db.get.return_value = expense

    response = await client.delete(f"/expenses/{expense.id}")

    assert response.status_code == 204
    assert expense.is_deactivated is True
    mock_db.delete.assert_not_called()
    mock_db.commit.assert_awaited_once()


async def test_delete_expense_not_found(client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.get.return_value = None

    response = await client.delete(f"/expenses/{uuid.uuid4()}")

    assert response.status_code == 404
