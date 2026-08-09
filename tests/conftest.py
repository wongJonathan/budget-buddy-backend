import datetime
import uuid
from collections.abc import AsyncGenerator, Callable, Sequence
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.main import app
from app.models.budget import Budget
from app.models.category import Category
from app.models.expense import Expense
from app.models.transaction import Transaction
from app.models.user import User


def _fake_refresh(obj: object) -> None:
    """Stands in for Postgres populating server-generated defaults on flush/refresh,
    since the mocked session never actually inserts anything. See checklist.md —
    these are plausible fakes, not what the real server_default/GENERATED values
    would be."""
    if getattr(obj, "id", None) is None:
        obj.id = uuid.uuid4()  # type: ignore[attr-defined]

    if isinstance(obj, Budget):
        if obj.is_deleted is None:
            obj.is_deleted = False
        if obj.created_at is None:
            obj.created_at = datetime.datetime.now(datetime.UTC)
    elif isinstance(obj, Expense):
        if obj.series_id is None:
            obj.series_id = uuid.uuid4()
        if obj.amount_saved is None:
            obj.amount_saved = Decimal("0")
        if obj.is_deactivated is None:
            obj.is_deactivated = False
        if obj.monthly_cost is None:
            # NOT COVERED: mocked session, see checklist.md — real value is a
            # Postgres GENERATED column, not computable here.
            obj.monthly_cost = obj.cost
    elif isinstance(obj, User):
        if obj.last_active is None:
            obj.last_active = datetime.date.today()
    elif isinstance(obj, Category | Transaction):
        pass  # no server-generated fields besides id


@pytest.fixture
def mock_db() -> MagicMock:
    """A MagicMock(spec=AsyncSession) standing in for the real DB connection.
    `spec=` means only real AsyncSession attributes are mockable, so a typo'd method
    name fails loudly instead of silently returning a fresh MagicMock.

    Routers and services run for real against this — only the DB I/O boundary is
    faked. See checklist.md for what that leaves unverified.
    """
    db = MagicMock(spec=AsyncSession)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.close = AsyncMock()
    # `add(obj)` takes the object, so defaults get populated right here; `flush()`
    # takes no object (it flushes everything pending in the session) so it can't
    # carry the same side effect — it's a no-op. `refresh(obj)` re-applies the same
    # fill-in, idempotently, for the common create_* pattern of add -> commit -> refresh.
    db.add = MagicMock(side_effect=_fake_refresh)
    db.refresh = AsyncMock(side_effect=_fake_refresh)
    db.flush = AsyncMock()
    db.delete = AsyncMock()
    db.get = AsyncMock(return_value=None)
    db.execute = AsyncMock()
    return db


@pytest.fixture
def make_scalars_result() -> Callable[[Sequence[object]], MagicMock]:
    """Builds a fake `Result` for configuring `mock_db.execute.return_value`, matching
    the `(await db.execute(select(...))).scalars().all()` pattern every list_* service
    function uses."""

    def _make(items: Sequence[object]) -> MagicMock:
        result = MagicMock()
        result.scalars.return_value.all.return_value = items
        return result

    return _make


@pytest.fixture
async def client(mock_db: MagicMock) -> AsyncGenerator[AsyncClient]:
    async def _override_get_session() -> AsyncGenerator[AsyncSession]:
        yield mock_db

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
