import datetime
import uuid
from collections.abc import Callable
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.schemas.fields import current_period, last_of_month
from tests.factories import make_expense, make_scalars_one, make_transaction


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
    # create_transaction verifies expense_id belongs to the caller and then re-reads the
    # Expense to find its fund; see test_ownership.py for the version a mock cannot fake.
    mock_db.scalars.return_value = make_scalars_one(make_expense(savings_id=None))

    response = await authed_client.post("/transactions", json=_transaction_payload())

    assert response.status_code == 201
    # A list for every type: a spend against a funded fund becomes two rows, and a
    # response shape that changed with the fund balance would be worse. docs/adr/0011.
    body = response.json()
    assert len(body) == 1
    assert body[0]["type"] == "spend"
    assert Decimal(body[0]["amount"]) == Decimal("42.50")
    assert body[0]["transfer_id"] is None
    mock_db.add_all.assert_called_once()
    mock_db.commit.assert_awaited_once()


async def test_an_expense_with_no_fund_produces_one_row(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    """The common case, and the reason `savings_id` is nullable: no fund, no split, and
    no special case at the call site."""
    mock_db.scalars.return_value = make_scalars_one(make_expense(savings_id=None))

    response = await authed_client.post("/transactions", json=_transaction_payload())

    assert [row["type"] for row in response.json()] == ["spend"]


async def test_a_client_cannot_post_a_transfer(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    """Server-only: a transfer is a linked pair written in one request, so a client able
    to post one row could leave half a transfer behind. docs/adr/0011."""
    mock_db.scalars.return_value = make_scalars_one(make_expense())

    response = await authed_client.post("/transactions", json=_transaction_payload(type="transfer"))

    assert response.status_code == 422


async def test_a_client_cannot_post_a_spend_saved(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    """Also server-only: the split is computed from a fund balance the client cannot see,
    so a declared `spend_saved` is unverifiable and can overdraw."""
    mock_db.scalars.return_value = make_scalars_one(make_expense())

    response = await authed_client.post(
        "/transactions", json=_transaction_payload(type="spend_saved")
    )

    assert response.status_code == 422


async def test_a_client_cannot_patch_a_spend_into_a_spend_saved(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    """The gap the update-side guard closes: without it this walks straight around the
    create-time check and overdraws the fund."""
    transaction = make_transaction()
    mock_db.scalars.return_value = make_scalars_one(transaction)

    response = await authed_client.patch(
        f"/transactions/{transaction.id}", json={"type": "spend_saved"}
    )

    assert response.status_code == 422


async def test_a_save_must_name_an_expense(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    """A fund belongs to an Expense lineage, so a bare save has nowhere to go."""
    response = await authed_client.post(
        "/transactions", json=_transaction_payload(type="save", expense_id=None)
    )

    assert response.status_code == 422


async def test_list_transactions(
    authed_client: AsyncClient, mock_db: MagicMock, make_scalars_result: Callable[..., MagicMock]
) -> None:
    transactions = [make_transaction(amount=Decimal("10")), make_transaction(amount=Decimal("20"))]
    mock_db.execute.return_value = make_scalars_result(transactions)
    mock_db.scalar.return_value = 2

    response = await authed_client.get("/transactions")

    assert response.status_code == 200
    body = response.json()
    amounts = {Decimal(t["amount"]) for t in body["items"]}
    assert amounts == {Decimal("10"), Decimal("20")}


async def test_list_transactions_reports_the_page_it_answered_with(
    authed_client: AsyncClient, mock_db: MagicMock, make_scalars_result: Callable[..., MagicMock]
) -> None:
    """`total` counts the whole window, not the page - it is what sizes a pager."""
    mock_db.execute.return_value = make_scalars_result([make_transaction()])
    mock_db.scalar.return_value = 412

    response = await authed_client.get("/transactions?limit=50&offset=100")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 412
    assert body["limit"] == 50
    assert body["offset"] == 100
    assert len(body["items"]) == 1


async def test_list_transactions_defaults_to_the_open_month(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    """Omitting both dates reads the open Period, not all time.

    Asserted on what reaches the service: the window is the point of the default, and an
    unbounded scan is exactly what the paging exists to prevent.
    """
    with patch(
        "app.routers.transactions.transaction_service.list_transactions",
        new=AsyncMock(return_value=([], 0)),
    ) as mock_list:
        response = await authed_client.get("/transactions")

    assert response.status_code == 200
    assert mock_list.call_args.kwargs["date_from"] == current_period()
    assert mock_list.call_args.kwargs["date_to"] == last_of_month(current_period())
    assert mock_list.call_args.kwargs["limit"] == 50
    assert mock_list.call_args.kwargs["offset"] == 0


async def test_list_transactions_defaults_each_end_independently(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    """A supplied `date_from` must not drag the other end back with it."""
    with patch(
        "app.routers.transactions.transaction_service.list_transactions",
        new=AsyncMock(return_value=([], 0)),
    ) as mock_list:
        response = await authed_client.get("/transactions?date_from=2026-01-01")

    assert response.status_code == 200
    assert mock_list.call_args.kwargs["date_from"] == datetime.date(2026, 1, 1)
    assert mock_list.call_args.kwargs["date_to"] == last_of_month(current_period())


async def test_list_transactions_passes_an_explicit_window_through(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    with patch(
        "app.routers.transactions.transaction_service.list_transactions",
        new=AsyncMock(return_value=([], 0)),
    ) as mock_list:
        response = await authed_client.get("/transactions?date_from=2026-03-01&date_to=2026-03-31")

    assert response.status_code == 200
    assert mock_list.call_args.kwargs["date_from"] == datetime.date(2026, 3, 1)
    assert mock_list.call_args.kwargs["date_to"] == datetime.date(2026, 3, 31)


async def test_list_transactions_rejects_a_backwards_window(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    """422, not an empty page - [] cannot be told apart from a real but empty window."""
    response = await authed_client.get("/transactions?date_from=2026-03-31&date_to=2026-03-01")

    assert response.status_code == 422
    assert "is after date_to" in response.json()["detail"]
    mock_db.execute.assert_not_awaited()


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "offset=-1", "date_from=nonsense"])
async def test_list_transactions_rejects_bad_paging(
    authed_client: AsyncClient, mock_db: MagicMock, query: str
) -> None:
    response = await authed_client.get(f"/transactions?{query}")

    assert response.status_code == 422


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
    assert len(body) == 1
    assert body[0]["expense_id"] is None
    assert body[0]["name"] == "Salary"
    mock_db.commit.assert_awaited_once()


async def test_an_explicit_null_expense_id_is_accepted(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    """Sending null is the same as omitting it - neither is a lookup for "not found"."""
    response = await authed_client.post(
        "/transactions", json=_transaction_payload(type="income", expense_id=None)
    )

    assert response.status_code == 201
    assert response.json()[0]["expense_id"] is None


async def test_a_transaction_without_a_name_is_rejected(authed_client: AsyncClient) -> None:
    """Required for every type, not only income: a row must always be identifiable."""
    payload = _transaction_payload()
    payload.pop("name")

    response = await authed_client.post("/transactions", json=payload)

    assert response.status_code == 422
