"""Tests for app/security/sessions.py.

These run against a real Postgres (see the "real-Postgres fixtures" section of
conftest.py). That is deliberate and load-bearing: these functions look sessions up by
`token_hash`, and `mock_db.get` returns whatever you told it to regardless of the key
it was handed. Mocked, this whole file passed while the lookup used the wrong column
and every logged-in request 500'd.
"""

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.models.sessions import Session
from app.models.user import User
from app.security.sessions import (
    create_session,
    new_token,
    resolve_session,
    revoke_sesion,
    token_digest,
)
from tests.factories import make_user


async def store_user(db_session: AsyncSession, **overrides: object) -> User:
    user = make_user(**overrides)
    db_session.add(user)
    await db_session.commit()
    return user


# ---------------------------------------------------------------------------
# token helpers
# ---------------------------------------------------------------------------


def test_token_digest_is_a_plain_sha256_of_the_token() -> None:
    assert token_digest("abc") == hashlib.sha256(b"abc").hexdigest()


def test_token_digest_is_stable_across_calls() -> None:
    """It's the lookup key, so an unstable digest would silently log everyone out."""
    assert token_digest("abc") == token_digest("abc")


def test_new_token_is_unpredictable_and_url_safe() -> None:
    tokens = {new_token() for _ in range(100)}

    assert len(tokens) == 100  # no repeats from a 32-byte urandom draw
    for token in tokens:
        assert token.isascii()
        assert "+" not in token and "/" not in token and "=" not in token


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------


async def test_create_session_stores_the_digest_and_never_the_raw_token(
    db_session: AsyncSession,
) -> None:
    """A stolen database dump must not hand over usable session tokens."""
    user = await store_user(db_session)

    token = await create_session(db_session, user, "pytest-agent")

    stored = (await db_session.scalars(select(Session))).one()
    assert stored.token_hash == token_digest(token)
    assert stored.token_hash != token
    assert token not in stored.token_hash


async def test_create_session_records_the_owner_and_user_agent(
    db_session: AsyncSession,
) -> None:
    user = await store_user(db_session)

    await create_session(db_session, user, "pytest-agent")

    stored = (await db_session.scalars(select(Session))).one()
    assert stored.user_id == user.id
    assert stored.user_agent == "pytest-agent"


async def test_create_session_expires_after_the_configured_ttl(
    db_session: AsyncSession,
) -> None:
    user = await store_user(db_session)
    before = datetime.now(UTC)

    await create_session(db_session, user, None)

    stored = (await db_session.scalars(select(Session))).one()
    expected = before + settings.session_ttl_days
    assert abs((stored.expires_at - expected).total_seconds()) < 5


async def test_two_logins_produce_two_independent_sessions(
    db_session: AsyncSession,
) -> None:
    """Signing in on a phone must not invalidate the laptop."""
    user = await store_user(db_session)

    first = await create_session(db_session, user, "phone")
    second = await create_session(db_session, user, "laptop")

    assert first != second
    stored = (await db_session.scalars(select(Session))).all()
    assert len(stored) == 2


# ---------------------------------------------------------------------------
# resolve_session
# ---------------------------------------------------------------------------


async def test_resolve_session_returns_the_owner_of_a_valid_token(
    db_session: AsyncSession,
) -> None:
    user = await store_user(db_session, display_name="Alice")
    token = await create_session(db_session, user, None)

    resolved = await resolve_session(db_session, token)

    assert resolved is not None
    assert resolved.id == user.id


async def test_resolve_session_rejects_a_token_that_was_never_issued(
    db_session: AsyncSession,
) -> None:
    assert await resolve_session(db_session, new_token()) is None


async def test_resolve_session_rejects_an_expired_session(
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The expiry check is the whole point of a TTL - a session that outlives
    expires_at is indistinguishable from one that never expires."""
    user = await store_user(db_session)
    token = await create_session(db_session, user, None)

    async with db_sessionmaker() as other:
        stored = (await other.scalars(select(Session))).one()
        stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await other.commit()

    assert await resolve_session(db_session, token) is None


# ---------------------------------------------------------------------------
# revoke_sesion  (sic - the spelling is the module's)
# ---------------------------------------------------------------------------


async def test_revoke_session_deletes_the_row(db_session: AsyncSession) -> None:
    user = await store_user(db_session)
    token = await create_session(db_session, user, None)

    await revoke_sesion(db_session, token)

    assert (await db_session.scalars(select(Session))).all() == []


async def test_revoke_session_ignores_a_token_that_does_not_exist(
    db_session: AsyncSession,
) -> None:
    """Logging out twice, or with a stale cookie, shouldn't 500 - which it did when a
    miss fell through to db.delete(None)."""
    await revoke_sesion(db_session, new_token())

    assert (await db_session.scalars(select(Session))).all() == []


async def test_revoke_session_leaves_other_sessions_alone(
    db_session: AsyncSession,
) -> None:
    """Logging out of one device must not sign the user out everywhere."""
    user = await store_user(db_session)
    phone = await create_session(db_session, user, "phone")
    await create_session(db_session, user, "laptop")

    await revoke_sesion(db_session, phone)

    remaining = (await db_session.scalars(select(Session))).all()
    assert [s.user_agent for s in remaining] == ["laptop"]
