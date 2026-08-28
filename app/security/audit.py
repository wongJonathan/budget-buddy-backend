"""Append-only auth audit trail.

Deliberately isolated from the rest of the request lifecycle: this module owns its
own engine and session factory so an audit write can never be undone by, or take
down, the request that triggered it. See `_engine` below for the why.
"""

import enum
import ipaddress
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi import Request
from sqlalchemy import CursorResult, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.database import IPAddress
from app.models.auth_events import AuthEvents

logger = logging.getLogger(__name__)

settings = get_settings()


class AuthEventType(enum.StrEnum):
    """The closed set of things worth recording.

    An enum rather than raw strings so a typo is an AttributeError at import time
    instead of a row that silently never matches a query.
    """

    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    LOGIN_BLOCKED = "login_blocked"
    LOGOUT = "logout"
    PASSWORD_CHANGED = "password_changed"
    PASSWORD_CHANGE_FAILED = "password_change_failed"
    SESSION_REVOKED = "session_revoked"
    SESSION_REJECTED = "session_rejected"
    USER_CREATED = "user_created"


# Audit events are written on failure paths that end in HTTPException, which rolls the
# request's transaction back. Sharing the request session would mean the rollback
# discards exactly the events this table exists to capture — so the audit trail gets
# its own connection, committed independently. The pool is small on purpose: this is a
# handful of tiny inserts per login, and it must not compete with request traffic for
# Postgres connections. pool_pre_ping because these connections sit idle between logins
# and are the most likely to have been dropped by the server or an intermediary.
_engine = create_async_engine(
    settings.database_url,
    pool_size=2,
    max_overflow=2,
    pool_pre_ping=True,
)
_audit_session_factory = async_sessionmaker(_engine, expire_on_commit=False)

MAX_USER_AGENT_LENGTH = 500

# Retention: most events age out at 90 days. These three are the ones you actually go
# looking for months later — brute-force patterns, "when did this password change",
# "when was this session killed" — so they get a year.
SHORT_RETENTION = timedelta(days=90)
LONG_RETENTION = timedelta(days=365)
LONG_RETENTION_EVENTS = frozenset(
    {
        AuthEventType.LOGIN_FAILURE,
        AuthEventType.PASSWORD_CHANGED,
        AuthEventType.SESSION_REVOKED,
    }
)

# `detail` is a JSONB free-for-all, which makes it exactly where a stray debug field
# ends up. Never put a password, raw session token, or token digest in it. Callers are
# responsible for that; this is a backstop, not a licence to be careless.
_FORBIDDEN_DETAIL_KEYS = frozenset(
    {"password", "new_password", "old_password", "token", "session_token", "token_hash", "digest"}
)


def client_ip(request: Request) -> str | None:
    """The caller's real IP, as best we can establish it.

    `request.client.host` on its own is useless here: every request arrives from
    cloudflared on localhost, so it would record 127.0.0.1 for every user forever.
    Cloudflare sets CF-Connecting-IP itself and it cannot be spoofed by the client
    through the tunnel, so it wins; X-Forwarded-For is the standard fallback for any
    other proxy in front, and its first entry is the original client.
    """
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()

    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first

    if request.client is not None:
        return request.client.host
    return None


def _parse_ip(raw: str | None) -> IPAddress | None:
    """Coerce a header value to something the INET column accepts."""
    if not raw:
        return None
    try:
        return ipaddress.ip_address(raw)
    except ValueError:
        # Header values are attacker-controlled. A garbage IP shouldn't cost us the
        # whole row, so drop just the address and keep the rest of the event.
        logger.warning("Unparsable client IP %r; recording event without it", raw)
        return None


def _clean_detail(detail: dict[str, Any] | None) -> dict[str, Any] | None:
    if not detail:
        return None
    safe = {k: v for k, v in detail.items() if k.lower() not in _FORBIDDEN_DETAIL_KEYS}
    if len(safe) != len(detail):
        logger.error(
            "Secret-looking key passed to audit detail for keys %s; dropped before storing",
            sorted(set(detail) - set(safe)),
        )
    return safe or None


async def record(
    event_type: AuthEventType,
    *,
    request: Request | None = None,
    user_id: uuid.UUID | None = None,
    email: str | None = None,
    session_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Write one audit event. Never raises.

    `request` is optional so scripts (e.g. USER_CREATED from CLI provisioning) can log
    an event with no IP or user agent.
    """
    try:
        ip: IPAddress | None = None
        user_agent: str | None = None
        if request is not None:
            ip = _parse_ip(client_ip(request))
            raw_user_agent = request.headers.get("User-Agent")
            if raw_user_agent:
                # Attacker-controlled and unbounded — truncate rather than reject, an
                # oversized header is not a reason to lose the event.
                user_agent = raw_user_agent[:MAX_USER_AGENT_LENGTH]

        async with _audit_session_factory() as session:
            session.add(
                AuthEvents(
                    event_type=event_type.value,
                    user_id=user_id,
                    email=email.strip().lower() if email else None,
                    ip=ip,
                    user_agent=user_agent,
                    session_id=session_id,
                    detail=_clean_detail(detail),
                )
            )
            await session.commit()
    except Exception:
        # A bare `except Exception` is normally a smell. It is correct here: the
        # alternative is a full disk or an exhausted pool taking down login. An audit
        # system that can break authentication is worse than a missing audit row.
        logger.exception("Failed to record auth event %s", event_type)


async def prune(db: AsyncSession) -> int:
    """Delete aged-out events. Returns the number of rows removed."""
    now = datetime.now(UTC)
    result = await db.execute(
        delete(AuthEvents).where(
            or_(
                AuthEvents.event_type.in_(LONG_RETENTION_EVENTS)
                & (AuthEvents.occurred_at < now - LONG_RETENTION),
                AuthEvents.event_type.notin_(LONG_RETENTION_EVENTS)
                & (AuthEvents.occurred_at < now - SHORT_RETENTION),
            )
        )
    )
    await db.commit()
    # AsyncSession.execute is typed as returning Result, but a DELETE always yields a
    # CursorResult, which is the only thing carrying rowcount.
    return cast(CursorResult[Any], result).rowcount


async def shutdown() -> None:
    """Dispose the audit engine's pool. Wire into the FastAPI lifespan."""
    await _engine.dispose()
