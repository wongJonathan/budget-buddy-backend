import datetime
import uuid
from collections.abc import Callable
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.schemas.fields import current_period
from tests.factories import make_budget, make_expense, make_scalars_one


def _expense_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "budget_id": str(uuid.uuid4()),
        "category_id": str(uuid.uuid4()),
        "name": "Groceries",
        "cost": "100.00",
        "frequency": "monthly",
        # Computed, not literal: only the open month is accepted, so a hard-coded
        # date would turn this whole file red on the 1st of some future month.
        "period": current_period().isoformat(),
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
    # No fund until money is saved against it - see docs/adr/0011.
    assert body["savings_id"] is None
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


async def test_a_mid_month_period_is_stored_as_the_first(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    """Anywhere inside the open month is accepted, and canonicalized on the way in."""
    mock_db.scalars.return_value = make_scalars_one(make_expense())
    mid_month = current_period().replace(day=17)

    response = await authed_client.post(
        "/expenses", json=_expense_payload(period=mid_month.isoformat())
    )

    assert response.status_code == 201
    assert response.json()["period"] == current_period().isoformat()


async def test_a_future_period_is_rejected(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    """Planning ahead is what draft Budgets are for - see docs/adr/0010."""
    mock_db.scalars.return_value = make_scalars_one(make_expense())
    period = current_period()
    ahead = period.replace(year=period.year + 1)

    response = await authed_client.post(
        "/expenses", json=_expense_payload(period=ahead.isoformat())
    )

    assert response.status_code == 422


async def test_a_past_period_is_rejected(authed_client: AsyncClient, mock_db: MagicMock) -> None:
    """Backdating would change a shortfall Rollover has already acted on."""
    mock_db.scalars.return_value = make_scalars_one(make_expense())
    period = current_period()
    behind = period.replace(year=period.year - 1)

    response = await authed_client.post(
        "/expenses", json=_expense_payload(period=behind.isoformat())
    )

    assert response.status_code == 422


async def test_patching_an_expense_into_another_period_is_rejected(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    """Otherwise the create-time restriction is one PATCH away from meaningless."""
    expense = make_expense()
    mock_db.scalars.return_value = make_scalars_one(expense)
    period = current_period()
    behind = period.replace(year=period.year - 1)

    response = await authed_client.patch(
        f"/expenses/{expense.id}", json={"period": behind.isoformat()}
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Router: GET /budgets/{budget_id}/expenses
#
# The budget-scoped listing. `{budget_id}` is a path parameter so `OwnedBudget`
# resolves it - ownership, the 404 and the soft-deleted-budget filter all come
# from the same dependency every other budget route uses, rather than from a
# hand-written check on a query parameter.
# ---------------------------------------------------------------------------


async def test_list_budget_expenses(
    authed_client: AsyncClient, mock_db: MagicMock, make_scalars_result: Callable[..., MagicMock]
) -> None:
    budget = make_budget()
    mock_db.scalars.return_value = make_scalars_one(budget)
    mock_db.execute.return_value = make_scalars_result(
        [make_expense(name="Milk"), make_expense(name="Bread")]
    )

    response = await authed_client.get(f"/budgets/{budget.id}/expenses")

    assert response.status_code == 200
    assert {e["name"] for e in response.json()} == {"Milk", "Bread"}


async def test_list_budget_expenses_empty_period_is_200_not_404(
    authed_client: AsyncClient, mock_db: MagicMock, make_scalars_result: Callable[..., MagicMock]
) -> None:
    """A budget with nothing planned for the period is a legitimate empty state -
    404 stays reserved for "that budget doesn't exist"."""
    budget = make_budget()
    mock_db.scalars.return_value = make_scalars_one(budget)
    mock_db.execute.return_value = make_scalars_result([])

    response = await authed_client.get(f"/budgets/{budget.id}/expenses")

    assert response.status_code == 200
    assert response.json() == []


async def test_list_budget_expenses_defaults_to_the_open_period(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    """Omitting `period` reads the open month.

    Asserted on the value reaching the service because FastAPI does not validate a
    parameter's default: the router has to resolve None itself, and a None arriving
    here would read `period IS NULL` and quietly return nothing.
    """
    budget = make_budget()
    mock_db.scalars.return_value = make_scalars_one(budget)

    with patch(
        "app.routers.expenses.expense_service.list_budget_expenses",
        new=AsyncMock(return_value=[]),
    ) as mock_list:
        response = await authed_client.get(f"/budgets/{budget.id}/expenses")

    assert response.status_code == 200
    _, called_budget_id, called_period, called_include_deleted = mock_list.call_args.args
    assert called_budget_id == budget.id
    assert called_period == current_period()
    assert called_include_deleted is False


async def test_list_budget_expenses_explicit_period_and_include_deleted(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    """A closed Period is readable - only *writing* to one is refused (ADR-0010)."""
    budget = make_budget()
    mock_db.scalars.return_value = make_scalars_one(budget)

    with patch(
        "app.routers.expenses.expense_service.list_budget_expenses",
        new=AsyncMock(return_value=[]),
    ) as mock_list:
        response = await authed_client.get(
            f"/budgets/{budget.id}/expenses?period=2026-03&include_deleted=true"
        )

    assert response.status_code == 200
    _, _, called_period, called_include_deleted = mock_list.call_args.args
    assert called_period == datetime.date(2026, 3, 1)
    assert called_include_deleted is True


@pytest.mark.parametrize(
    "period",
    [
        "not-a-period",
        # Rejected rather than truncated: the reads filter on `period =`, so accepting
        # a day-of-month would return [] for a budget that does have that month
        # planned - the silent mismatch app/schemas/fields.py exists to prevent.
        "2026-03-15",
        "",
    ],
)
async def test_list_budget_expenses_rejects_a_malformed_period(
    authed_client: AsyncClient, mock_db: MagicMock, period: str
) -> None:
    budget = make_budget()
    mock_db.scalars.return_value = make_scalars_one(budget)

    response = await authed_client.get(f"/budgets/{budget.id}/expenses?period={period}")

    assert response.status_code == 422
    # 422 with a `loc`, like every other validation failure - not a bare 400.
    assert response.json()["detail"][0]["loc"] == ["query", "period"]


async def test_list_budget_expenses_accepts_an_unpadded_month(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    """`2026-3` is unambiguous, and strptime canonicalizes it to the same 1st."""
    budget = make_budget()
    mock_db.scalars.return_value = make_scalars_one(budget)

    with patch(
        "app.routers.expenses.expense_service.list_budget_expenses",
        new=AsyncMock(return_value=[]),
    ) as mock_list:
        response = await authed_client.get(f"/budgets/{budget.id}/expenses?period=2026-3")

    assert response.status_code == 200
    assert mock_list.call_args.args[2] == datetime.date(2026, 3, 1)


async def test_list_budget_expenses_budget_not_found(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    """Someone else's budget and a nonexistent one are both 404, never an empty list."""
    mock_db.scalars.return_value = make_scalars_one(None)

    response = await authed_client.get(f"/budgets/{uuid.uuid4()}/expenses")

    assert response.status_code == 404
    mock_db.execute.assert_not_awaited()


async def test_list_budget_expenses_requires_a_session(
    client: AsyncClient, mock_db: MagicMock
) -> None:
    """`mock_db` finds a budget, so a 401 can only come from the auth dependency."""
    mock_db.scalars.return_value = make_scalars_one(make_budget())

    response = await client.get(f"/budgets/{uuid.uuid4()}/expenses")

    assert response.status_code == 401
