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
from unittest.mock import MagicMock

from fastapi import Request

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
        # NOT NULL in the real table, so these have to be present for any test that
        # actually inserts the row. The email is randomised because it's UNIQUE.
        "email": f"user-{uuid.uuid4().hex[:12]}@example.com",
        "hashed_password": "not-a-real-hash",
    }
    defaults.update(overrides)
    return User(**defaults)


def make_request(
    headers: dict[str, str] | None = None,
    method: str = "POST",
    client: tuple[str, int] | None = ("127.0.0.1", 54321),
) -> Request:
    """A Request built straight from an ASGI scope - no app, no transport.

    For unit-testing the handful of functions that read raw headers or the peer
    address (`audit.client_ip`, `csrf.origin_check`) without standing up a client.
    """
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/",
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
            "client": client,
        }
    )


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
        "is_deleted": False,
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
        "is_deleted": False,
    }
    defaults.update(overrides)
    return Transaction(**defaults)


def make_scalars_one(obj: object | None) -> MagicMock:
    """A fake `ScalarResult` for stubbing `mock_db.scalars`.

    Distinct from the `make_scalars_result` fixture, which fakes the `Result` returned
    by `db.execute` and is consumed as `.scalars().all()`. This one fakes what
    `db.scalars` returns directly, consumed as `.one_or_none()` - the shape every
    get_* service uses now that fetch-by-id goes through a visibility-filtered
    select() rather than db.get().
    """
    result = MagicMock()
    result.one_or_none.return_value = obj
    result.first.return_value = obj
    result.all.return_value = [] if obj is None else [obj]
    return result
