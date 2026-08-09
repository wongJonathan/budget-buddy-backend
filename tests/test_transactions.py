import uuid
from collections.abc import Callable
from decimal import Decimal
from unittest.mock import MagicMock

from httpx import AsyncClient

from tests.factories import make_transaction


def _transaction_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "expense_id": str(uuid.uuid4()),
        "type": "spend",
        "amount": "42.50",
        "date": "2026-08-01",
    }
    payload.update(overrides)
    return payload


async def test_create_transaction(client: AsyncClient, mock_db: MagicMock) -> None:
    response = await client.post("/transactions", json=_transaction_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "spend"
    assert Decimal(body["amount"]) == Decimal("42.50")
    assert body["transfer_id"] is None
    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited_once()


async def test_create_transfer_transaction(client: AsyncClient, mock_db: MagicMock) -> None:
    transfer_id = uuid.uuid4()
    response = await client.post(
        "/transactions", json=_transaction_payload(type="transfer", transfer_id=str(transfer_id))
    )

    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "transfer"
    assert body["transfer_id"] == str(transfer_id)


async def test_list_transactions(
    client: AsyncClient, mock_db: MagicMock, make_scalars_result: Callable[..., MagicMock]
) -> None:
    transactions = [make_transaction(amount=Decimal("10")), make_transaction(amount=Decimal("20"))]
    mock_db.execute.return_value = make_scalars_result(transactions)

    response = await client.get("/transactions")

    assert response.status_code == 200
    amounts = {Decimal(t["amount"]) for t in response.json()}
    assert amounts == {Decimal("10"), Decimal("20")}


async def test_get_transaction_found(client: AsyncClient, mock_db: MagicMock) -> None:
    transaction = make_transaction(note="Weekly shop")
    mock_db.get.return_value = transaction

    response = await client.get(f"/transactions/{transaction.id}")

    assert response.status_code == 200
    assert response.json()["note"] == "Weekly shop"


async def test_get_transaction_not_found(client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.get.return_value = None

    response = await client.get(f"/transactions/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_update_transaction_found(client: AsyncClient, mock_db: MagicMock) -> None:
    transaction = make_transaction(amount=Decimal("10"))
    mock_db.get.return_value = transaction

    response = await client.patch(f"/transactions/{transaction.id}", json={"amount": "15.00"})

    assert response.status_code == 200
    assert Decimal(response.json()["amount"]) == Decimal("15.00")
    mock_db.commit.assert_awaited_once()


async def test_update_transaction_not_found(client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.get.return_value = None

    response = await client.patch(f"/transactions/{uuid.uuid4()}", json={"amount": "15.00"})

    assert response.status_code == 404


async def test_delete_transaction_found(client: AsyncClient, mock_db: MagicMock) -> None:
    transaction = make_transaction()
    mock_db.get.return_value = transaction

    response = await client.delete(f"/transactions/{transaction.id}")

    assert response.status_code == 204
    mock_db.delete.assert_awaited_once_with(transaction)
    mock_db.commit.assert_awaited_once()


async def test_delete_transaction_not_found(client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.get.return_value = None

    response = await client.delete(f"/transactions/{uuid.uuid4()}")

    assert response.status_code == 404
