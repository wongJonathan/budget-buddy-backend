import asyncio
import datetime
import os
import uuid
from collections.abc import AsyncGenerator, Callable, Sequence
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.models  # noqa: F401  registers every model on Base.metadata
from app.config import get_settings
from app.database import Base, get_db_session
from app.dependencies import get_current_user
from app.main import app
from app.models.budget import Budget
from app.models.category import Category
from app.models.expense import Expense
from app.models.savings import Savings
from app.models.transaction import Transaction
from app.models.user import User
from tests.factories import make_scalars_one, make_user


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
        if obj.is_deleted is None:
            obj.is_deleted = False
        if obj.monthly_cost is None:
            # NOT COVERED: mocked session, see checklist.md — real value is a
            # Postgres GENERATED column, not computable here.
            obj.monthly_cost = obj.cost
    elif isinstance(obj, User):
        if obj.last_active is None:
            obj.last_active = datetime.date.today()
    elif isinstance(obj, Transaction | Savings):
        if obj.is_deleted is None:
            obj.is_deleted = False
    elif isinstance(obj, Category):
        pass  # no server-generated fields besides id


def _scalars_result(items: Sequence[object]) -> MagicMock:
    """A fake `Result` matching the `(await db.execute(select(...))).scalars().all()`
    pattern every list_* service function uses."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


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
    # Defaults to an empty result rather than a bare AsyncMock. A bare one hands back
    # another AsyncMock, so `result.scalars()` returns an un-awaited coroutine and the
    # service dies with "'coroutine' object has no attribute 'all'" — which points at
    # the service, not at the test that forgot to stub the query. Tests that care
    # override this with `make_scalars_result`.
    db.execute = AsyncMock(return_value=_scalars_result([]))
    # Fetch-by-id now goes through db.scalars(select(...)) rather than db.get, so this
    # needs a default for the same reason execute does - a bare AsyncMock would hand
    # back a coroutine from .one_or_none().
    db.scalars = AsyncMock(return_value=make_scalars_one(None))
    return db


@pytest.fixture
def make_scalars_result() -> Callable[[Sequence[object]], MagicMock]:
    """Builds a fake `Result` for configuring `mock_db.execute.return_value`."""
    return _scalars_result


@pytest.fixture
async def client(mock_db: MagicMock) -> AsyncGenerator[AsyncClient]:
    async def _override_get_session() -> AsyncGenerator[AsyncSession]:
        yield mock_db

    app.dependency_overrides[get_db_session] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def current_user() -> User:
    """The account an `authed_client` request is authenticated as."""
    return make_user(display_name="Alice")


@pytest.fixture
async def authed_client(
    mock_db: MagicMock, current_user: User
) -> AsyncGenerator[AsyncClient]:
    """A client whose requests arrive already authenticated as `current_user`.

    Overrides `get_current_user` rather than planting a session cookie: resolving a
    real cookie would run sessions.py against the mocked DB, which is a different
    module's job to test. Use the plain `client` fixture to assert the 401 path.
    """

    async def _override_get_session() -> AsyncGenerator[AsyncSession]:
        yield mock_db

    async def _override_current_user() -> User:
        return current_user

    app.dependency_overrides[get_db_session] = _override_get_session
    app.dependency_overrides[get_current_user] = _override_current_user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# real-Postgres fixtures
# ---------------------------------------------------------------------------
# The mocked `mock_db` above is fine for routers and services whose logic is pure
# Python, but it cannot see anything the database itself decides: constraints,
# ON DELETE behaviour, whether a lookup key is even the right *column*. A mocked
# `db.get(User, email)` happily returns whatever you set, while the real one raises
# DataError trying to parse an email as a UUID. Anything asserting on those needs a
# real connection, so it uses the fixtures below.
#
# They run against a dedicated `<db>_test` database, created on first use, so a
# failing run can't leave junk in the dev data. Override with TEST_DATABASE_URL.


def _resolve_test_database_url() -> str:
    override = os.environ.get("TEST_DATABASE_URL")
    if override:
        # Explicit opt-in. Note the schema is dropped and rebuilt on it every run.
        return override
    url = make_url(get_settings().database_url)
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


def _guard_against_wiping_a_real_database(url: str) -> None:
    """_provision drops every table, so refuse anything that isn't clearly a test DB."""
    name = make_url(url).database or ""
    if not name.endswith("_test") and not os.environ.get("TEST_DATABASE_URL"):
        raise RuntimeError(
            f"Refusing to drop tables in {name!r}: expected a database whose name ends "
            "in '_test'. Set TEST_DATABASE_URL if this is deliberate."
        )


async def _provision(url: str) -> None:
    _guard_against_wiping_a_real_database(url)
    target = make_url(url)

    # CREATE DATABASE can't run inside a transaction block, hence AUTOCOMMIT, and it
    # has to be issued from a connection to some *other* database.
    admin = create_async_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            )
            if not exists:
                # The name comes from our own config, not from user input.
                await conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        await admin.dispose()

    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            # Wipe the schema before rebuilding. create_all only creates *missing*
            # tables and never alters existing ones, so without this the database
            # silently keeps whatever schema it was first built with and every later
            # model change surfaces as a confusing "column does not exist".
            #
            # DROP SCHEMA rather than metadata.drop_all: users and budgets reference
            # each other, and drop_all can't order a cycle (the same circular-FK
            # problem the migrations have). Dropping the schema sidesteps ordering,
            # and takes the enum types with it so create_all can remake them.
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """Create the test database and schema once per run.

    Synchronous, driving its own loop via asyncio.run, because pytest-asyncio gives
    each test a fresh event loop - a session-scoped *async* fixture would leave an
    engine bound to a loop that closes before the second test runs.
    """
    url = _resolve_test_database_url()
    asyncio.run(_provision(url))
    return url


@pytest.fixture
async def db_engine(test_database_url: str) -> AsyncGenerator[AsyncEngine]:
    """An engine on the test database, emptied again on teardown.

    Cleanup hangs off this fixture rather than an autouse one so that the mocked
    tests, which are the majority, never open a connection at all.
    """
    engine = create_async_engine(test_database_url)
    tables = ", ".join(f'"{name}"' for name in Base.metadata.tables)
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            # CASCADE because users <-> budgets reference each other; RESTART IDENTITY
            # so auth_events ids don't drift between tests.
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        await engine.dispose()


@pytest.fixture
def db_sessionmaker(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def db_session(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession]:
    async with db_sessionmaker() as session:
        yield session
