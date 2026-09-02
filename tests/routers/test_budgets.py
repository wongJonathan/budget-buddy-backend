"""Tests for app/routers/budgets.py.

Every route sits behind `CurrentUser`, so these use `authed_client`; the 401
path is asserted separately at the bottom with the plain `client`. The service
behind `POST /budgets/json-convert-budget` is tested in
tests/services/test_budget.py.
"""

import datetime
import json
import uuid
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.models.user import User
from tests.factories import make_budget, make_expense, make_scalars_one

# ---------------------------------------------------------------------------
# Router: create / read / update / delete
#
# Every route here sits behind `CurrentUser`, so these use `authed_client`.
# The 401 path is asserted separately, at the bottom, with the plain `client`.
# ---------------------------------------------------------------------------


async def test_create_budget(
    authed_client: AsyncClient, mock_db: MagicMock, current_user: User
) -> None:
    response = await authed_client.post("/budgets", json={"name": "Groceries Budget"})

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Groceries Budget"
    assert body["is_deleted"] is False
    # Ownership comes from the session, not the payload - BudgetCreate has no user_id.
    assert body["user_id"] == str(current_user.id)
    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited_once()


async def test_update_budget_found(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    budget = make_budget(name="Old name")
    mock_db.scalars.return_value = make_scalars_one(budget)

    response = await authed_client.patch(f"/budgets/{budget.id}", json={"name": "New name"})

    assert response.status_code == 200
    assert response.json()["name"] == "New name"
    mock_db.commit.assert_awaited_once()


async def test_update_budget_not_found(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.scalars.return_value = make_scalars_one(None)

    response = await authed_client.patch(f"/budgets/{uuid.uuid4()}", json={"name": "New name"})

    assert response.status_code == 404


async def test_delete_budget_is_soft_delete(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    budget = make_budget(is_deleted=False)
    mock_db.scalars.return_value = make_scalars_one(budget)

    response = await authed_client.delete(f"/budgets/{budget.id}")

    assert response.status_code == 204
    assert budget.is_deleted is True
    mock_db.delete.assert_not_called()
    mock_db.commit.assert_awaited_once()


async def test_delete_budget_not_found(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.scalars.return_value = make_scalars_one(None)

    response = await authed_client.delete(f"/budgets/{uuid.uuid4()}")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Router: GET /budgets/{id} (period + include_deleted)
# ---------------------------------------------------------------------------


async def test_get_budget_not_found(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    mock_db.scalars.return_value = make_scalars_one(None)

    response = await authed_client.get(f"/budgets/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_get_budget_invalid_period_format(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    budget = make_budget()
    mock_db.scalars.return_value = make_scalars_one(budget)

    response = await authed_client.get(f"/budgets/{budget.id}?period=not-a-period")

    assert response.status_code == 400


async def test_get_budget_with_expenses(
    authed_client: AsyncClient, mock_db: MagicMock, make_scalars_result: Callable[..., MagicMock]
) -> None:
    budget = make_budget(name="Groceries Budget")
    mock_db.scalars.return_value = make_scalars_one(budget)
    mock_db.execute.return_value = make_scalars_result(
        [make_expense(name="Milk"), make_expense(name="Bread")]
    )

    response = await authed_client.get(f"/budgets/{budget.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Groceries Budget"
    assert {e["name"] for e in body["expenses"]} == {"Milk", "Bread"}


async def test_get_budget_no_expenses_this_period_is_200_not_404(
    authed_client: AsyncClient, mock_db: MagicMock, make_scalars_result: Callable[..., MagicMock]
) -> None:
    """A budget with nothing planned for the resolved period is a legitimate empty
    state, not a 404 — 404 stays reserved for 'budget doesn't exist'."""
    budget = make_budget()
    mock_db.scalars.return_value = make_scalars_one(budget)
    mock_db.execute.return_value = make_scalars_result([])

    response = await authed_client.get(f"/budgets/{budget.id}")

    assert response.status_code == 200
    assert response.json()["expenses"] == []


async def test_get_budget_default_period_is_today(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    budget = make_budget()
    mock_db.scalars.return_value = make_scalars_one(budget)

    with patch(
        "app.routers.budgets.list_budget_expenses", new=AsyncMock(return_value=[])
    ) as mock_list_expenses:
        response = await authed_client.get(f"/budgets/{budget.id}")

    assert response.status_code == 200
    _, called_budget_id, called_period, called_include_deleted = mock_list_expenses.call_args.args
    assert called_budget_id == budget.id
    assert called_period == datetime.date.today()
    assert called_include_deleted is False


async def test_get_budget_explicit_period_and_include_deleted(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    budget = make_budget()
    mock_db.scalars.return_value = make_scalars_one(budget)

    with patch(
        "app.routers.budgets.list_budget_expenses", new=AsyncMock(return_value=[])
    ) as mock_list_expenses:
        response = await authed_client.get(
            f"/budgets/{budget.id}?period=2026-03&include_deleted=true"
        )

    assert response.status_code == 200
    _, _, called_period, called_include_deleted = mock_list_expenses.call_args.args
    assert called_period == datetime.date(2026, 3, 1)
    assert called_include_deleted is True


# ---------------------------------------------------------------------------
# Router: authentication
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/budgets"),
        ("GET", "/budgets/{budget_id}"),
        ("PATCH", "/budgets/{budget_id}"),
        ("DELETE", "/budgets/{budget_id}"),
        ("POST", "/budgets/json-convert-budget"),
    ],
)
async def test_budget_routes_require_a_session(
    client: AsyncClient, mock_db: MagicMock, method: str, path: str
) -> None:
    """No route on this router is reachable without a session.

    `mock_db` is stubbed to return a budget, so a 401 here can only come from the
    auth dependency - not from the row being missing.
    """
    mock_db.scalars.return_value = make_scalars_one(make_budget())

    response = await client.request(
        method, path.format(budget_id=uuid.uuid4()), json={"name": "Groceries Budget"}
    )

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Router: POST /budgets/json-convert-budget
# ---------------------------------------------------------------------------


def _expenses_json(**entries: dict[str, object]) -> bytes:
    return json.dumps(entries).encode()


async def test_json_convert_budget_endpoint(
    authed_client: AsyncClient, mock_db: MagicMock, make_scalars_result: Callable[..., MagicMock]
) -> None:
    mock_db.execute.return_value = make_scalars_result([])
    meta = json.dumps({"name": "Imported Budget"})
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

    response = await authed_client.post(
        "/budgets/json-convert-budget",
        data={"meta": meta},
        files={"file": ("expenses.json", file_bytes, "application/json")},
    )

    assert response.status_code == 201
    assert response.json()["name"] == "Imported Budget"


async def test_json_convert_budget_endpoint_missing_key_is_error(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    meta = json.dumps({"name": "Imported Budget"})
    file_bytes = _expenses_json(e1={"tag": "Groceries", "name": "Weekly shop"})

    response = await authed_client.post(
        "/budgets/json-convert-budget",
        data={"meta": meta},
        files={"file": ("expenses.json", file_bytes, "application/json")},
    )

    assert response.status_code == 400
    assert "missing required field" in response.json()["detail"]
    mock_db.commit.assert_not_awaited()
