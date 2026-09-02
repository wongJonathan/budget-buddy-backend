"""Tests for app/services/budget.py.

The JSON import and the frequency parser, exercised directly rather than through
`POST /budgets/json-convert-budget` - the router half lives in
tests/routers/test_budgets.py.
"""

import json
from collections.abc import Callable
from unittest.mock import MagicMock

import pytest

from app.models.user import User
from app.schemas.budget import BudgetCreate
from app.services import budget as budget_service
from app.services.budget import _get_frequency
from tests.factories import make_category

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
    mock_db: MagicMock, make_scalars_result: Callable[..., MagicMock], current_user: User
) -> None:
    mock_db.execute.return_value = make_scalars_result([])  # no existing categories
    metadata = BudgetCreate(name="Imported Budget")
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

    budget = await budget_service.convert_json_to_budget(
        mock_db, metadata, file_bytes, current_user
    )

    assert budget.name == "Imported Budget"
    # 1 budget + 2 categories + 2 expenses (the transactionType row is skipped)
    assert mock_db.add.call_count == 5
    mock_db.commit.assert_awaited_once()


async def test_convert_json_to_budget_reuses_existing_category(
    mock_db: MagicMock, make_scalars_result: Callable[..., MagicMock], current_user: User
) -> None:
    existing = make_category(user_id=current_user.id, name="Groceries")
    mock_db.execute.return_value = make_scalars_result([existing])
    metadata = BudgetCreate(name="Imported Budget")
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

    await budget_service.convert_json_to_budget(mock_db, metadata, file_bytes, current_user)

    # 1 budget + 1 expense, no new category (Groceries already existed)
    assert mock_db.add.call_count == 2


async def test_convert_json_to_budget_missing_required_key_raises(
    mock_db: MagicMock, current_user: User
) -> None:
    metadata = BudgetCreate(name="Imported Budget")
    # missing cost/frequency/amountSaved
    file_bytes = _expenses_json(e1={"tag": "Groceries", "name": "Weekly shop"})

    with pytest.raises(ValueError, match="missing required field"):
        await budget_service.convert_json_to_budget(mock_db, metadata, file_bytes, current_user)

    mock_db.commit.assert_not_awaited()
