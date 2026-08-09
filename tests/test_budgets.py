import datetime
import json
import uuid
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.services import budget as budget_service
from app.services.budget import _get_frequency
from tests.factories import make_budget, make_category, make_expense

# ---------------------------------------------------------------------------
# Router: create / list / update / delete
# ---------------------------------------------------------------------------


async def test_create_budget(client: AsyncClient, mock_db: MagicMock) -> None:
    user_id = uuid.uuid4()
    response = await client.post(
        "/budgets", json={"user_id": str(user_id), "name": "Groceries Budget"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Groceries Budget"
    assert body["is_deleted"] is False
    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited_once()


async def test_list_budgets(
    client: AsyncClient, mock_db: MagicMock, make_scalars_result: Callable[..., MagicMock]
) -> None:
    budgets = [make_budget(name="A"), make_budget(name="B")]
    mock_db.execute.return_value = make_scalars_result(budgets)

    response = await client.get("/budgets")

    assert response.status_code == 200
    names = {b["name"] for b in response.json()}
    assert names == {"A", "B"}


async def test_update_budget_found(client: AsyncClient, mock_db: MagicMock) -> None:
    budget = make_budget(name="Old name")
    mock_db.get.return_value = budget

    response = await client.patch(f"/budgets/{budget.id}", json={"name": "New name"})

    assert response.status_code == 200
    assert response.json()["name"] == "New name"
    mock_db.commit.assert_awaited_once()


async def test_update_budget_not_found(client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.get.return_value = None

    response = await client.patch(f"/budgets/{uuid.uuid4()}", json={"name": "New name"})

    assert response.status_code == 404


async def test_delete_budget_is_soft_delete(client: AsyncClient, mock_db: MagicMock) -> None:
    budget = make_budget(is_deleted=False)
    mock_db.get.return_value = budget

    response = await client.delete(f"/budgets/{budget.id}")

    assert response.status_code == 204
    assert budget.is_deleted is True
    mock_db.delete.assert_not_called()
    mock_db.commit.assert_awaited_once()


async def test_delete_budget_not_found(client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.get.return_value = None

    response = await client.delete(f"/budgets/{uuid.uuid4()}")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Router: GET /budgets/{id} (period + include_deleted)
# ---------------------------------------------------------------------------


async def test_get_budget_not_found(client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.get.return_value = None

    response = await client.get(f"/budgets/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_get_budget_invalid_period_format(client: AsyncClient, mock_db: MagicMock) -> None:
    budget = make_budget()
    mock_db.get.return_value = budget

    response = await client.get(f"/budgets/{budget.id}?period=not-a-period")

    assert response.status_code == 400


async def test_get_budget_with_expenses(
    client: AsyncClient, mock_db: MagicMock, make_scalars_result: Callable[..., MagicMock]
) -> None:
    budget = make_budget(name="Groceries Budget")
    mock_db.get.return_value = budget
    mock_db.execute.return_value = make_scalars_result(
        [make_expense(name="Milk"), make_expense(name="Bread")]
    )

    response = await client.get(f"/budgets/{budget.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Groceries Budget"
    assert {e["name"] for e in body["expenses"]} == {"Milk", "Bread"}


async def test_get_budget_no_expenses_this_period_is_200_not_404(
    client: AsyncClient, mock_db: MagicMock, make_scalars_result: Callable[..., MagicMock]
) -> None:
    """A budget with nothing planned for the resolved period is a legitimate empty
    state, not a 404 — 404 stays reserved for 'budget doesn't exist'."""
    budget = make_budget()
    mock_db.get.return_value = budget
    mock_db.execute.return_value = make_scalars_result([])

    response = await client.get(f"/budgets/{budget.id}")

    assert response.status_code == 200
    assert response.json()["expenses"] == []


async def test_get_budget_default_period_is_today(client: AsyncClient, mock_db: MagicMock) -> None:
    budget = make_budget()
    mock_db.get.return_value = budget

    with patch(
        "app.routers.budgets.list_budget_expenses", new=AsyncMock(return_value=[])
    ) as mock_list_expenses:
        response = await client.get(f"/budgets/{budget.id}")

    assert response.status_code == 200
    _, called_budget_id, called_period, called_include_deleted = mock_list_expenses.call_args.args
    assert called_budget_id == budget.id
    assert called_period == datetime.date.today()
    assert called_include_deleted is False


async def test_get_budget_explicit_period_and_include_deleted(
    client: AsyncClient, mock_db: MagicMock
) -> None:
    budget = make_budget()
    mock_db.get.return_value = budget

    with patch(
        "app.routers.budgets.list_budget_expenses", new=AsyncMock(return_value=[])
    ) as mock_list_expenses:
        response = await client.get(f"/budgets/{budget.id}?period=2026-03&include_deleted=true")

    assert response.status_code == 200
    _, _, called_period, called_include_deleted = mock_list_expenses.call_args.args
    assert called_period == datetime.date(2026, 3, 1)
    assert called_include_deleted is True


# ---------------------------------------------------------------------------
# Service: _get_frequency (pure function, no DB involved)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Once", budget_service.Frequency.ONCE),
        ("Daily", budget_service.Frequency.DAILY),
        ("Weekly", budget_service.Frequency.WEEKLY),
        ("Monthly", budget_service.Frequency.MONTHLY),
        ("Yearly", budget_service.Frequency.YEARLY),
        ("Set date", budget_service.Frequency.ONCE),
    ],
)
def test_get_frequency_recognized(raw: str, expected: budget_service.Frequency) -> None:
    assert _get_frequency(raw) == expected


def test_get_frequency_unrecognized_raises() -> None:
    with pytest.raises(ValueError, match="not recognized"):
        _get_frequency("Fortnightly")


# ---------------------------------------------------------------------------
# Service: convert_json_to_budget
# ---------------------------------------------------------------------------


def _expenses_json(**entries: dict[str, object]) -> bytes:
    return json.dumps(entries).encode()


async def test_convert_json_to_budget_creates_budget_categories_expenses(
    mock_db: MagicMock, make_scalars_result: Callable[..., MagicMock]
) -> None:
    from app.schemas.budget import BudgetCreate

    mock_db.execute.return_value = make_scalars_result([])  # no existing categories
    metadata = BudgetCreate(user_id=uuid.uuid4(), name="Imported Budget")
    file_bytes = _expenses_json(
        e1={
            "tag": "Groceries",
            "name": "Weekly shop",
            "cost": "50.00",
            "frequency": "Monthly",
            "amountSaved": "0",
            "note": None,
        },
        e2={
            "tag": "Rent",
            "name": "Rent",
            "cost": "1200",
            "frequency": "Monthly",
            "amountSaved": "0",
            "note": None,
        },
        skip_me={"transactionType": "expense", "amount": "10"},
    )

    budget = await budget_service.convert_json_to_budget(mock_db, metadata, file_bytes)

    assert budget.name == "Imported Budget"
    # 1 budget + 2 categories + 2 expenses (the transactionType row is skipped)
    assert mock_db.add.call_count == 5
    mock_db.commit.assert_awaited_once()


async def test_convert_json_to_budget_reuses_existing_category(
    mock_db: MagicMock, make_scalars_result: Callable[..., MagicMock]
) -> None:
    from app.schemas.budget import BudgetCreate

    user_id = uuid.uuid4()
    existing = make_category(user_id=user_id, name="Groceries")
    mock_db.execute.return_value = make_scalars_result([existing])
    metadata = BudgetCreate(user_id=user_id, name="Imported Budget")
    file_bytes = _expenses_json(
        e1={
            "tag": "Groceries",
            "name": "Weekly shop",
            "cost": "50.00",
            "frequency": "Monthly",
            "amountSaved": "0",
            "note": None,
        },
    )

    await budget_service.convert_json_to_budget(mock_db, metadata, file_bytes)

    # 1 budget + 1 expense, no new category (Groceries already existed)
    assert mock_db.add.call_count == 2


async def test_convert_json_to_budget_missing_required_key_raises(mock_db: MagicMock) -> None:
    from app.schemas.budget import BudgetCreate

    metadata = BudgetCreate(user_id=uuid.uuid4(), name="Imported Budget")
    # missing cost/frequency/amountSaved
    file_bytes = _expenses_json(e1={"tag": "Groceries", "name": "Weekly shop"})

    with pytest.raises(ValueError, match="missing required field"):
        await budget_service.convert_json_to_budget(mock_db, metadata, file_bytes)

    mock_db.commit.assert_not_awaited()


async def test_json_convert_budget_endpoint(
    client: AsyncClient, mock_db: MagicMock, make_scalars_result: Callable[..., MagicMock]
) -> None:
    mock_db.execute.return_value = make_scalars_result([])
    meta = json.dumps({"user_id": str(uuid.uuid4()), "name": "Imported Budget"})
    file_bytes = _expenses_json(
        e1={
            "tag": "Groceries",
            "name": "Weekly shop",
            "cost": "50.00",
            "frequency": "Monthly",
            "amountSaved": "0",
            "note": None,
        },
    )

    response = await client.post(
        "/budgets/json-convert-budget",
        data={"meta": meta},
        files={"file": ("expenses.json", file_bytes, "application/json")},
    )

    assert response.status_code == 201
    assert response.json()["name"] == "Imported Budget"


async def test_json_convert_budget_endpoint_missing_key_is_error(
    client: AsyncClient, mock_db: MagicMock
) -> None:
    meta = json.dumps({"user_id": str(uuid.uuid4()), "name": "Imported Budget"})
    file_bytes = _expenses_json(e1={"tag": "Groceries", "name": "Weekly shop"})

    response = await client.post(
        "/budgets/json-convert-budget",
        data={"meta": meta},
        files={"file": ("expenses.json", file_bytes, "application/json")},
    )

    assert response.status_code == 400
    assert "missing required field" in response.json()["detail"]
    mock_db.commit.assert_not_awaited()
