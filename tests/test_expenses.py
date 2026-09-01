import uuid
from collections.abc import Callable
from decimal import Decimal
from unittest.mock import MagicMock

from httpx import AsyncClient

from tests.factories import make_expense, make_scalars_one


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


async def test_create_expense(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    # create_expense verifies budget_id and category_id belong to the caller, so
    # the mocked lookup has to find something. The real check is covered against
    # Postgres in test_ownership.py - a mock can only say "a row came back".
    mock_db.scalars.return_value = make_scalars_one(make_expense())

    response = await authed_client.post("/expenses", json=_expense_payload())

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
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    mock_db.scalars.return_value = make_scalars_one(make_expense())

    response = await authed_client.post("/expenses", json=_expense_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["note"] is None
    assert body["goal_amount"] is None
    assert body["goal_date"] is None
    assert body["is_deleted"] is False


async def test_list_expenses(
    authed_client: AsyncClient, mock_db: MagicMock, make_scalars_result: Callable[..., MagicMock]
) -> None:
    expenses = [make_expense(name="Groceries"), make_expense(name="Rent")]
    mock_db.execute.return_value = make_scalars_result(expenses)

    response = await authed_client.get("/expenses")

    assert response.status_code == 200
    names = {e["name"] for e in response.json()}
    assert names == {"Groceries", "Rent"}


async def test_get_expense_found(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    expense = make_expense(name="Groceries")
    mock_db.scalars.return_value = make_scalars_one(expense)

    response = await authed_client.get(f"/expenses/{expense.id}")

    assert response.status_code == 200
    assert response.json()["name"] == "Groceries"


async def test_get_expense_not_found(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.scalars.return_value = make_scalars_one(None)

    response = await authed_client.get(f"/expenses/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_update_expense_found(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    expense = make_expense(name="Groceries")
    mock_db.scalars.return_value = make_scalars_one(expense)

    response = await authed_client.patch(f"/expenses/{expense.id}", json={"name": "Food"})

    assert response.status_code == 200
    assert response.json()["name"] == "Food"
    mock_db.commit.assert_awaited_once()


async def test_update_expense_not_found(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.scalars.return_value = make_scalars_one(None)

    response = await authed_client.patch(f"/expenses/{uuid.uuid4()}", json={"name": "Food"})

    assert response.status_code == 404


async def test_delete_expense_is_soft_delete(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    expense = make_expense(is_deleted=False)
    mock_db.scalars.return_value = make_scalars_one(expense)

    response = await authed_client.delete(f"/expenses/{expense.id}")

    assert response.status_code == 204
    assert expense.is_deleted is True
    mock_db.delete.assert_not_called()
    mock_db.commit.assert_awaited_once()


async def test_delete_expense_not_found(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.scalars.return_value = make_scalars_one(None)

    response = await authed_client.delete(f"/expenses/{uuid.uuid4()}")

    assert response.status_code == 404
