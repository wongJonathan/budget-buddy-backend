"""Tests for app/services/provisioning.py.

Against a real Postgres (see the "real-Postgres fixtures" section of conftest.py),
because most of what provisioning has to get right is enforced by the database: the
circular FK between users and budgets, the UNIQUE constraint on email, and the fact
that the audit row is written on a *separate* connection from the one being committed.
None of that is observable through a mocked session.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.auth_events import AuthEvents
from app.models.budget import Budget
from app.models.user import User
from app.security import audit
from app.security.passwords import verify_and_update_password
from app.services.provisioning import (
    DEFAULT_BUDGET_NAME,
    EmailAlreadyExists,
    provision_user,
)

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def audit_to_test_db(db_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the audit module's own session factory at the test database.

    Necessary rather than incidental: `audit._engine` is built at import time against
    the dev database, and asyncpg connections belong to the loop that opened them.
    Without this the USER_CREATED rows would land somewhere these tests can't see.
    """
    monkeypatch.setattr(audit, "_audit_session_factory", async_sessionmaker(db_engine))


async def fetch_events(db_session: AsyncSession) -> list[AuthEvents]:
    return list((await db_session.scalars(select(AuthEvents))).all())


# ---------------------------------------------------------------------------
# the happy path
# ---------------------------------------------------------------------------


async def test_provision_user_creates_the_account(db_session: AsyncSession) -> None:
    user, _ = await provision_user(
        db_session, display_name="Alice", email="alice@example.com", password=PASSWORD
    )

    stored = (await db_session.scalars(select(User))).one()
    assert stored.id == user.id
    assert stored.display_name == "Alice"
    assert stored.email == "alice@example.com"


async def test_provision_user_stores_a_hash_not_the_password(
    db_session: AsyncSession,
) -> None:
    await provision_user(
        db_session, display_name="Alice", email="alice@example.com", password=PASSWORD
    )

    stored = (await db_session.scalars(select(User))).one()
    assert PASSWORD not in stored.hashed_password
    is_valid, _ = verify_and_update_password(PASSWORD, stored.hashed_password)
    assert is_valid


async def test_provision_user_normalizes_the_email(db_session: AsyncSession) -> None:
    """The stored form has to match what login looks up, or the account is unusable."""
    await provision_user(
        db_session, display_name="Alice", email="  ALICE@Example.COM  ", password=PASSWORD
    )

    stored = (await db_session.scalars(select(User))).one()
    assert stored.email == "alice@example.com"


async def test_provision_user_creates_a_blank_budget(db_session: AsyncSession) -> None:
    _, budget = await provision_user(
        db_session, display_name="Alice", email="alice@example.com", password=PASSWORD
    )

    stored = (await db_session.scalars(select(Budget))).one()
    assert stored.id == budget.id
    assert stored.name == DEFAULT_BUDGET_NAME
    assert stored.deleted_at is None


async def test_provision_user_wires_the_circular_reference_both_ways(
    db_session: AsyncSession,
) -> None:
    """budgets.user_id and users.active_budget_id point at each other; a partial wiring
    would leave an account whose default budget isn't actually its active one."""
    await provision_user(
        db_session, display_name="Alice", email="alice@example.com", password=PASSWORD
    )

    stored_user = (await db_session.scalars(select(User))).one()
    stored_budget = (await db_session.scalars(select(Budget))).one()
    assert stored_budget.user_id == stored_user.id
    assert stored_user.active_budget_id == stored_budget.id


# ---------------------------------------------------------------------------
# duplicates
# ---------------------------------------------------------------------------


async def test_provision_user_rejects_an_email_that_already_exists(
    db_session: AsyncSession,
) -> None:
    await provision_user(
        db_session, display_name="Alice", email="alice@example.com", password=PASSWORD
    )

    with pytest.raises(EmailAlreadyExists):
        await provision_user(
            db_session, display_name="Impostor", email="alice@example.com", password=PASSWORD
        )


async def test_the_duplicate_check_sees_through_casing(db_session: AsyncSession) -> None:
    """Normalization happens before the check, so a differently-cased address is the
    same account - not a second one the UNIQUE constraint would then reject anyway."""
    await provision_user(
        db_session, display_name="Alice", email="alice@example.com", password=PASSWORD
    )

    with pytest.raises(EmailAlreadyExists):
        await provision_user(
            db_session, display_name="Alice", email="ALICE@EXAMPLE.COM", password=PASSWORD
        )


async def test_a_rejected_duplicate_leaves_nothing_behind(db_session: AsyncSession) -> None:
    await provision_user(
        db_session, display_name="Alice", email="alice@example.com", password=PASSWORD
    )

    with pytest.raises(EmailAlreadyExists):
        await provision_user(
            db_session, display_name="Impostor", email="alice@example.com", password=PASSWORD
        )

    assert len((await db_session.scalars(select(User))).all()) == 1
    assert len((await db_session.scalars(select(Budget))).all()) == 1


# ---------------------------------------------------------------------------
# the audit trail
# ---------------------------------------------------------------------------


async def test_provisioning_records_one_user_created_event(
    db_session: AsyncSession,
) -> None:
    user, _ = await provision_user(
        db_session, display_name="Alice", email="alice@example.com", password=PASSWORD
    )

    (event,) = await fetch_events(db_session)
    assert event.event_type == "user_created"
    assert event.user_id == user.id
    assert event.email == "alice@example.com"


async def test_the_audit_event_carries_no_request_details(
    db_session: AsyncSession,
) -> None:
    """A script has no IP and no user agent - this is the `request=None` path, and
    those columns staying null is what marks the row as script-origin."""
    await provision_user(
        db_session, display_name="Alice", email="alice@example.com", password=PASSWORD
    )

    (event,) = await fetch_events(db_session)
    assert event.ip is None
    assert event.user_agent is None
    assert event.detail is None


async def test_nothing_is_audited_when_provisioning_fails(
    db_session: AsyncSession,
) -> None:
    """The ordering rule: record() commits on its own session, so calling it before the
    transaction commits would leave a USER_CREATED event for a user that never existed.
    A duplicate is the failure that's easiest to trigger on purpose."""
    await provision_user(
        db_session, display_name="Alice", email="alice@example.com", password=PASSWORD
    )
    events_after_success = await fetch_events(db_session)

    with pytest.raises(EmailAlreadyExists):
        await provision_user(
            db_session, display_name="Impostor", email="alice@example.com", password=PASSWORD
        )

    assert await fetch_events(db_session) == events_after_success
