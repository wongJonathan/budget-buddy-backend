import uuid
from collections.abc import Callable
from decimal import Decimal
from unittest.mock import MagicMock

from httpx import AsyncClient

from tests.factories import make_scalars_one, make_transaction


def _transaction_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "expense_id": str(uuid.uuid4()),
        "type": "spend",
        "name": "Weekly shop",
        "amount": "42.50",
        "date": "2026-08-01",
    }
    payload.update(overrides)
    return payload


async def test_create_transaction(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    # create_transaction verifies expense_id belongs to the caller; see
    # test_ownership.py for the version of this that a mock cannot fake.
    mock_db.scalars.return_value = make_scalars_one(make_transaction())

    response = await authed_client.post("/transactions", json=_transaction_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "spend"
    assert Decimal(body["amount"]) == Decimal("42.50")
    assert body["transfer_id"] is None
    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited_once()


async def test_create_transfer_transaction(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.scalars.return_value = make_scalars_one(make_transaction())
    transfer_id = uuid.uuid4()
    response = await authed_client.post(
        "/transactions", json=_transaction_payload(type="transfer", transfer_id=str(transfer_id))
    )

    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "transfer"
    assert body["transfer_id"] == str(transfer_id)


async def test_list_transactions(
    authed_client: AsyncClient, mock_db: MagicMock, make_scalars_result: Callable[..., MagicMock]
) -> None:
    transactions = [make_transaction(amount=Decimal("10")), make_transaction(amount=Decimal("20"))]
    mock_db.execute.return_value = make_scalars_result(transactions)

    response = await authed_client.get("/transactions")

    assert response.status_code == 200
    amounts = {Decimal(t["amount"]) for t in response.json()}
    assert amounts == {Decimal("10"), Decimal("20")}


async def test_get_transaction_found(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    transaction = make_transaction(note="Weekly shop")
    mock_db.scalars.return_value = make_scalars_one(transaction)

    response = await authed_client.get(f"/transactions/{transaction.id}")

    assert response.status_code == 200
    assert response.json()["note"] == "Weekly shop"


async def test_get_transaction_not_found(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.scalars.return_value = make_scalars_one(None)

    response = await authed_client.get(f"/transactions/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_update_transaction_found(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    transaction = make_transaction(amount=Decimal("10"))
    mock_db.scalars.return_value = make_scalars_one(transaction)

    response = await authed_client.patch(
        f"/transactions/{transaction.id}", json={"amount": "15.00"}
    )

    assert response.status_code == 200
    assert Decimal(response.json()["amount"]) == Decimal("15.00")
    mock_db.commit.assert_awaited_once()


async def test_update_transaction_not_found(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.scalars.return_value = make_scalars_one(None)

    response = await authed_client.patch(f"/transactions/{uuid.uuid4()}", json={"amount": "15.00"})

    assert response.status_code == 404


async def test_delete_transaction_is_soft_delete(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    transaction = make_transaction(is_deleted=False)
    mock_db.scalars.return_value = make_scalars_one(transaction)

    response = await authed_client.delete(f"/transactions/{transaction.id}")

    assert response.status_code == 204
    assert transaction.is_deleted is True
    mock_db.delete.assert_not_called()  # the row stays; only the flag moves
    mock_db.commit.assert_awaited_once()


async def test_delete_transaction_not_found(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.scalars.return_value = make_scalars_one(None)

    response = await authed_client.delete(f"/transactions/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_create_income_without_an_expense(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    """Income is a Transaction with no Expense - docs/adr/0009.

    Nothing is looked up, because there is no parent id to own: `verify_owned_refs`
    skips a null reference rather than treating it as a miss.
    """
    payload = _transaction_payload(type="income", name="Salary")
    payload.pop("expense_id")

    response = await authed_client.post("/transactions", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["expense_id"] is None
    assert body["name"] == "Salary"
    mock_db.commit.assert_awaited_once()


async def test_an_explicit_null_expense_id_is_accepted(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    """Sending null is the same as omitting it - neither is a lookup for "not found"."""
    response = await authed_client.post(
        "/transactions", json=_transaction_payload(type="income", expense_id=None)
    )

    assert response.status_code == 201
    assert response.json()["expense_id"] is None


async def test_a_transaction_without_a_name_is_rejected(authed_client: AsyncClient) -> None:
    """Required for every type, not only income: a row must always be identifiable."""
    payload = _transaction_payload()
    payload.pop("name")

    response = await authed_client.post("/transactions", json=payload)

    assert response.status_code == 422
