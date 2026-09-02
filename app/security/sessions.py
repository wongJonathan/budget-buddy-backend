import hashlib
import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Cookie, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.sessions import Session
from app.models.user import User


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(db: AsyncSession, user: User, user_agent: str | None) -> str:
    session_token = new_token()
    digest = token_digest(session_token)
    session = Session(
        token_hash=digest,
        expires_at=datetime.now(UTC) + settings.session_ttl_days,
        user_id=user.id,
        user_agent=user_agent,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session_token


async def session_by_token(db: AsyncSession, token: str) -> Session | None:
    """Find a session by its token.

    Not db.get() - that takes a primary key, and this table's is `id`, a UUID.
    `token_hash` is a separate UNIQUE column, and Postgres rejects a SHA-256 digest as
    a UUID outright rather than just failing to match.
    """
    result = await db.scalars(select(Session).where(Session.token_hash == token_digest(token)))
    # one_or_none rather than first: token_hash is UNIQUE, so a second row would mean
    # the constraint is gone, and that should be loud rather than silently picking one.
    return result.one_or_none()


async def resolve_session(db: AsyncSession, token: str) -> User | None:
    # TODO: sliding expiry - when expires_at is roughly half spent, re-issue the cookie
    # so an active session doesn't get logged out mid-use.
    matching_session = await session_by_token(db, token)

    if not matching_session or matching_session.expires_at < datetime.now(UTC):
        return None
    # This one *is* a primary key lookup - users.id is what user_id references.
    return await db.get(User, matching_session.user_id)


async def revoke_sesion(db: AsyncSession, token: str) -> None:
    matching_session = await session_by_token(db, token)
    if matching_session is None:
        # Logging out twice, or with a cookie whose session already expired, is a
        # no-op rather than an error.
        return

    await db.delete(matching_session)
    await db.commit()


def revoke_all_for_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    """
    Revokes all tokens for all devices for a user.
    Used during password change
    """
    pass


async def get_session_token(
    token: Annotated[str | None, Cookie(alias=settings.cookie_name)] = None,
) -> str | None:
    return token


SessionToken = Annotated[str | None, Depends(get_session_token)]
