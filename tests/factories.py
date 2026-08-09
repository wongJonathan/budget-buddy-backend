"""Builders for fully-populated fake ORM instances, used as stand-ins for rows a real
Postgres query would have returned (e.g. as `mock_db.get`/`execute` return values).

Every field is given a default so the resulting object always satisfies its Read schema
without a real DB round-trip. Server-generated values (ids, timestamps, monthly_cost) are
plausible fakes, not what Postgres would actually compute — see checklist.md.
"""

import datetime
import uuid
from decimal import Decimal
from typing import Any

from app.models.budget import Budget
from app.models.category import Category
from app.models.enums import Frequency, TransactionType
from app.models.expense import Expense
from app.models.transaction import Transaction
from app.models.user import User


def make_user(**overrides: Any) -> User:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "display_name": "Test User",
        "active_budget_id": None,
        "last_active": datetime.date.today(),
    }
    defaults.update(overrides)
    return User(**defaults)


def make_budget(**overrides: Any) -> Budget:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "name": "Test Budget",
        "note": None,
        "is_deleted": False,
        "created_at": datetime.datetime.now(datetime.UTC),
    }
    defaults.update(overrides)
    return Budget(**defaults)


def make_category(**overrides: Any) -> Category:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "name": "Test Category",
        "system_type": None,
    }
    defaults.update(overrides)
    return Category(**defaults)


def make_expense(**overrides: Any) -> Expense:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "budget_id": uuid.uuid4(),
        "category_id": uuid.uuid4(),
        "series_id": uuid.uuid4(),
        "name": "Test Expense",
        "note": None,
        "cost": Decimal("10.00"),
        "frequency": Frequency.MONTHLY,
        # NOT COVERED: mocked session, see checklist.md — monthly_cost is a Postgres
        # GENERATED column in reality; this is a plausible fake, not computed SQL.
        "monthly_cost": Decimal("10.00"),
        "amount_saved": Decimal("0"),
        "goal_amount": None,
        "goal_date": None,
        "period": datetime.date.today(),
        "is_deactivated": False,
    }
    defaults.update(overrides)
    return Expense(**defaults)


def make_transaction(**overrides: Any) -> Transaction:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "expense_id": uuid.uuid4(),
        "type": TransactionType.SPEND,
        "amount": Decimal("10.00"),
        "note": None,
        "date": datetime.date.today(),
        "transfer_id": None,
    }
    defaults.update(overrides)
    return Transaction(**defaults)
