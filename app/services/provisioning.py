"""Account provisioning.

Its own module rather than a function in `services/user.py`: provisioning spans User
and Budget, and it owns the write ordering that the circular FK between those two
tables forces. When provisioning misbehaves, that ordering is the first thing you want
to read, and it should be in one file rather than split across two entity modules.

There is no self-service equivalent - accounts are created by `app.scripts.create_user`
and nothing else. See docs/adr/0006-accounts-provisioned-by-script.md.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.budget import Budget
from app.models.user import User
from app.schemas.fields import normalize_email
from app.security import audit
from app.security.audit import AuthEventType
from app.security.passwords import hash_password
from app.services.user import get_user_by_email

DEFAULT_BUDGET_NAME = "My Budget"


class EmailAlreadyExists(AppError):
    def __init__(self, email: str, existing_id: uuid.UUID) -> None:
        super().__init__(
            f"A user with email {email} already exists (id: {existing_id})",
            status_code=409,
        )


async def provision_user(
    db: AsyncSession, *, display_name: str, email: str, password: str
) -> tuple[User, Budget]:
    """Create one account, with a blank Budget already set as its active one.

    Returns the User and the Budget. Raises EmailAlreadyExists if the address is taken.
    """
    normalized = normalize_email(email)

    existing = await get_user_by_email(db, normalized)
    if existing is not None:
        raise EmailAlreadyExists(normalized, existing.id)

    # users.active_budget_id and budgets.user_id reference each other, so neither row
    # can be written complete in one go. Insert the User with a null pointer, flush to
    # get its id, insert the Budget against it, then fill the pointer in - all inside
    # one transaction, so a failure at any step leaves nothing behind.
    user = User(
        display_name=display_name,
        email=normalized,
        hashed_password=hash_password(password),
    )
    db.add(user)
    await db.flush()

    budget = Budget(user_id=user.id, name=DEFAULT_BUDGET_NAME)
    db.add(budget)
    await db.flush()

    # Seeding the pointer directly rather than going through Activation: a blank Budget
    # has no Expense rows to instantiate, so there is nothing for Activation to do.
    user.active_budget_id = budget.id

    await db.commit()

    # After the commit, never before. audit.record commits on its own session by
    # design, so recording first and then failing the commit would leave a USER_CREATED
    # event describing a user that does not exist. This is the opposite of the rule on
    # the login failure paths, where the whole point is to outlive a rollback.
    await audit.record(AuthEventType.USER_CREATED, user_id=user.id, email=normalized)

    return user, budget
