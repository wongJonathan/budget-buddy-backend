"""Tests for the auth audit trail.

These run against a real Postgres, unlike the rest of the suite, which mocks the DB.
Two of the properties this module exists for - a write surviving the request's
rollback, and ON DELETE SET NULL preserving the row - are behaviours of the database
itself, so a mocked session could not observe either.

The `db_engine` / test-database fixtures live in conftest.py - see the "real-Postgres
fixtures" section there for how the dedicated `<db>_test` database is set up.
"""

import ipaddress
import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.models.auth_events import AuthEvents
from app.models.user import User
from app.security import audit
from app.security.audit import AuthEventType
from tests.factories import make_request

LONG_USER_AGENT = "Mozilla/5.0 " + "x" * 600


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def audit_engine(
    test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[AsyncEngine]:
    """Repoint the audit module's own factory at the test database.

    Needed for isolation, but also unavoidable mechanically: `audit._engine` is built
    at import time and asyncpg connections belong to the loop that opened them, so the
    module-level engine can't be shared across per-test loops.
    """
    engine = create_async_engine(test_database_url, pool_size=2, max_overflow=2, pool_pre_ping=True)
    monkeypatch.setattr(audit, "_engine", engine)
    monkeypatch.setattr(audit, "_audit_session_factory", async_sessionmaker(engine))
    yield engine
    await engine.dispose()


async def fetch_events(engine: AsyncEngine) -> list[AuthEvents]:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        return list((await session.scalars(select(AuthEvents).order_by(AuthEvents.id))).all())


# ---------------------------------------------------------------------------
# the separate session factory
# ---------------------------------------------------------------------------


async def test_record_survives_a_rolled_back_request_transaction(
    db_engine: AsyncEngine, audit_engine: AsyncEngine
) -> None:
    """The reason this module owns its own session factory.

    Simulates a failed login: the request has written something, records the failure,
    then raises HTTPException and gets rolled back. The audit row must outlive it.
    If `record()` is ever changed to reuse the request's session, this is the only
    test in the suite that notices.
    """
    request_sessions = async_sessionmaker(db_engine, expire_on_commit=False)

    async with request_sessions() as request_session:
        request_session.add(
            User(display_name="Rollback", email="rollback@example.com", hashed_password="x")
        )
        await request_session.flush()  # written, still inside the open transaction

        await audit.record(AuthEventType.LOGIN_FAILURE, email="rollback@example.com")

        await request_session.rollback()  # what an HTTPException-terminated request does

    events = await fetch_events(db_engine)
    assert [(e.event_type, e.email) for e in events] == [("login_failure", "rollback@example.com")]

    # The other half of the claim: the request's own write really was discarded, so
    # the audit row surviving isn't an artifact of nothing having rolled back.
    async with db_engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM users")) == 0


async def test_record_logs_and_returns_when_the_database_is_unreachable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An audit system that can break authentication is worse than a missing row."""
    broken = create_async_engine("postgresql+asyncpg://postgres:postgres@127.0.0.1:1/nowhere")
    monkeypatch.setattr(audit, "_audit_session_factory", async_sessionmaker(broken))

    try:
        with caplog.at_level(logging.ERROR, logger="app.security.audit"):
            await audit.record(AuthEventType.LOGIN_SUCCESS, email="nobody@example.com")
    finally:
        await broken.dispose()

    assert "Failed to record auth event" in caplog.text


# ---------------------------------------------------------------------------
# client_ip
# ---------------------------------------------------------------------------


def test_client_ip_prefers_cf_connecting_ip() -> None:
    """Everything arrives from cloudflared on localhost, so the peer address is noise."""
    request = make_request(
        {"CF-Connecting-IP": "203.0.113.7", "X-Forwarded-For": "198.51.100.9"},
        client=("127.0.0.1", 54321),
    )
    assert audit.client_ip(request) == "203.0.113.7"


def test_client_ip_falls_back_through_forwarded_for_to_the_peer_address() -> None:
    forwarded = make_request({"X-Forwarded-For": "198.51.100.9, 70.41.3.18, 150.172.238.178"})
    assert audit.client_ip(forwarded) == "198.51.100.9"

    bare = make_request()
    assert audit.client_ip(bare) == "127.0.0.1"

    # ASGI allows a scope with no client at all (e.g. an in-process test transport).
    assert audit.client_ip(make_request(client=None)) is None


# ---------------------------------------------------------------------------
# record()
# ---------------------------------------------------------------------------


async def test_record_works_without_a_request(
    db_engine: AsyncEngine, audit_engine: AsyncEngine
) -> None:
    """The CLI provisioning path: no request, so no IP and no user agent."""
    await audit.record(
        AuthEventType.USER_CREATED,
        email="  MiXeD.Case@Example.COM  ",
        detail={"source": "cli"},
    )

    (event,) = await fetch_events(db_engine)
    assert event.event_type == "user_created"
    assert event.email == "mixed.case@example.com"  # stripped and lowercased
    assert event.ip is None
    assert event.user_agent is None
    assert event.detail == {"source": "cli"}


async def test_record_truncates_an_oversized_user_agent(
    db_engine: AsyncEngine, audit_engine: AsyncEngine
) -> None:
    """Attacker-controlled and unbounded - truncated, never a reason to drop the event."""
    request = make_request({"CF-Connecting-IP": "203.0.113.7", "User-Agent": LONG_USER_AGENT})

    await audit.record(AuthEventType.LOGIN_SUCCESS, request=request)

    (event,) = await fetch_events(db_engine)
    assert event.user_agent is not None
    assert len(event.user_agent) == audit.MAX_USER_AGENT_LENGTH
    assert event.user_agent == LONG_USER_AGENT[: audit.MAX_USER_AGENT_LENGTH]
    assert event.ip == ipaddress.ip_address("203.0.113.7")


async def test_deleting_a_user_keeps_their_audit_rows(
    db_engine: AsyncEngine, audit_engine: AsyncEngine
) -> None:
    """ON DELETE SET NULL, not CASCADE: the trail outlives the account it describes."""
    sessions = async_sessionmaker(db_engine, expire_on_commit=False)
    async with sessions() as session:
        user = User(display_name="Doomed", email="doomed@example.com", hashed_password="x")
        session.add(user)
        await session.commit()
        user_id = user.id

    await audit.record(AuthEventType.LOGIN_SUCCESS, user_id=user_id, email="doomed@example.com")

    async with sessions() as session:
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()

    (event,) = await fetch_events(db_engine)
    assert event.user_id is None
    assert event.email == "doomed@example.com"  # still says who it was about


# ---------------------------------------------------------------------------
# prune()
# ---------------------------------------------------------------------------


async def test_prune_respects_the_two_retention_windows(db_engine: AsyncEngine) -> None:
    now = datetime.now(UTC)
    sessions = async_sessionmaker(db_engine, expire_on_commit=False)

    async with sessions() as session:
        session.add_all(
            [
                AuthEvents(event_type=AuthEventType.LOGOUT, occurred_at=now - timedelta(days=100)),
                AuthEvents(event_type=AuthEventType.LOGOUT, occurred_at=now - timedelta(days=1)),
                AuthEvents(
                    event_type=AuthEventType.LOGIN_FAILURE, occurred_at=now - timedelta(days=100)
                ),
                AuthEvents(
                    event_type=AuthEventType.LOGIN_FAILURE, occurred_at=now - timedelta(days=400)
                ),
            ]
        )
        await session.commit()

        deleted = await audit.prune(session)

    assert deleted == 2
    remaining = {(e.event_type, (now - e.occurred_at).days) for e in await fetch_events(db_engine)}
    assert remaining == {
        ("logout", 1),  # inside the 90-day window
        ("login_failure", 100),  # past 90 days, but kept for a year
    }
