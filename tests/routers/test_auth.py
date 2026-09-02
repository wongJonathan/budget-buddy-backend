"""Tests for app/routers/auth.py and app/schemas/auth.py.

Against a real Postgres, for the reason spelled out in test_sessions.py: login finds
the account by email and `/me` finds the session by token, and a mocked `db.get`
returns a row no matter which column you claim to be searching.

`auth.router` is not registered in main.py yet, so these mount it on a throwaway app
rather than going through `app`. That keeps the wiring decision yours, and it means
these tests start passing the moment the router is included, without being rewritten.
"""

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.database import get_db_session
from app.models.sessions import Session
from app.models.user import User
from app.routers import auth
from app.schemas.auth import LoginRequest
from app.security.passwords import hash_password
from app.security.sessions import create_session
from tests.factories import make_user

PASSWORD = "correct horse battery staple"


@pytest.fixture
async def auth_client(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient]:
    test_app = FastAPI()
    test_app.include_router(auth.router)

    async def _override_get_session() -> AsyncGenerator[AsyncSession]:
        async with db_sessionmaker() as session:
            yield session

    test_app.dependency_overrides[get_db_session] = _override_get_session
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def registered_user(db_session: AsyncSession) -> User:
    user = make_user(
        display_name="Alice",
        email="alice@example.com",
        hashed_password=hash_password(PASSWORD),
    )
    db_session.add(user)
    await db_session.commit()
    return user


# ---------------------------------------------------------------------------
# schemas
# ---------------------------------------------------------------------------


def test_login_request_keeps_the_password_out_of_reprs() -> None:
    """SecretStr is what stops a password reaching a log line or a traceback frame."""
    data = LoginRequest(email="alice@example.com", password=PASSWORD)  # type: ignore[arg-type]

    assert PASSWORD not in repr(data)
    assert PASSWORD not in str(data.password)
    assert data.password.get_secret_value() == PASSWORD


def test_login_request_requires_both_fields() -> None:
    with pytest.raises(ValueError):
        LoginRequest(email="alice@example.com")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------


async def test_login_succeeds_and_sets_the_session_cookie(
    auth_client: AsyncClient, registered_user: User
) -> None:
    response = await auth_client.post(
        "/auth/login", json={"email": "alice@example.com", "password": PASSWORD}
    )

    assert response.status_code == 200
    assert response.json()["email"] == "alice@example.com"
    assert settings.cookie_name in response.cookies


async def test_login_normalises_the_submitted_email(
    auth_client: AsyncClient, registered_user: User
) -> None:
    """The route lowercases and strips before lookup, so this must find the account."""
    response = await auth_client.post(
        "/auth/login", json={"email": "  ALICE@Example.COM  ", "password": PASSWORD}
    )

    assert response.status_code == 200


async def test_login_rejects_a_wrong_password(
    auth_client: AsyncClient, registered_user: User
) -> None:
    response = await auth_client.post(
        "/auth/login", json={"email": "alice@example.com", "password": "not it"}
    )

    assert response.status_code == 401
    assert settings.cookie_name not in response.cookies


async def test_login_rejects_an_unknown_email_the_same_way(auth_client: AsyncClient) -> None:
    """Same 401 as a wrong password: the response must not reveal which accounts exist.
    The route also verifies against a dummy hash so the timing doesn't reveal it either."""
    response = await auth_client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
    )

    assert response.status_code == 401


async def test_login_creates_exactly_one_session_row(
    auth_client: AsyncClient, registered_user: User, db_session: AsyncSession
) -> None:
    await auth_client.post("/auth/login", json={"email": "alice@example.com", "password": PASSWORD})

    stored = (await db_session.scalars(select(Session))).all()
    assert len(stored) == 1
    assert stored[0].user_id == registered_user.id


async def test_login_rejects_a_malformed_body(auth_client: AsyncClient) -> None:
    response = await auth_client.post("/auth/login", json={"email": "alice@example.com"})

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------


async def test_logout_without_a_cookie_still_clears_it(auth_client: AsyncClient) -> None:
    """No session to revoke, but the response must still tell the browser to drop the
    cookie - otherwise a stale cookie survives every logout."""
    response = await auth_client.post("/auth/logout")

    assert response.status_code == 200
    assert "Max-Age=0" in response.headers.get("set-cookie", "")


async def test_logout_revokes_the_session_it_was_given(
    auth_client: AsyncClient, registered_user: User, db_session: AsyncSession
) -> None:
    token = await create_session(db_session, registered_user, "pytest-agent")
    auth_client.cookies.set(settings.cookie_name, token)

    response = await auth_client.post("/auth/logout")

    assert response.status_code == 200
    assert (await db_session.scalars(select(Session))).all() == []


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------


async def test_me_requires_a_session(auth_client: AsyncClient) -> None:
    response = await auth_client.get("/auth/me")

    assert response.status_code == 401


async def test_me_returns_the_signed_in_user(
    auth_client: AsyncClient, registered_user: User, db_session: AsyncSession
) -> None:
    token = await create_session(db_session, registered_user, "pytest-agent")
    auth_client.cookies.set(settings.cookie_name, token)

    response = await auth_client.get("/auth/me")

    assert response.status_code == 200
    assert response.json()["email"] == "alice@example.com"


async def test_me_rejects_a_token_that_was_never_issued(auth_client: AsyncClient) -> None:
    """A forged cookie must be a 401, not a 500 - and definitely not a 200."""
    auth_client.cookies.set(settings.cookie_name, "not-a-real-token")

    response = await auth_client.get("/auth/me")

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /auth/change-password
# ---------------------------------------------------------------------------


async def test_change_password_requires_a_session(auth_client: AsyncClient) -> None:
    """The handler is still a stub, but the CurrentUser dependency is already wired,
    so the auth gate in front of it is worth locking down now."""
    response = await auth_client.post("/auth/change-password")

    assert response.status_code == 401
