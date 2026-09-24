import uuid
from collections.abc import Awaitable, Callable
from inspect import Parameter, Signature
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models.budget import Budget
from app.models.category import Category
from app.models.expense import Expense
from app.models.transaction import Transaction
from app.models.user import User
from app.ownership import require_owned
from app.security.sessions import SessionToken, resolve_session

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


async def get_current_user(
    db: DbSession,
    session: SessionToken,
) -> User:
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    user = await resolve_session(db, session)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def owned_path[T](
    model: type[T], param_name: str, *, allow_deleted: bool = False
) -> Callable[..., Awaitable[T]]:
    """Resolve a path parameter to a row the caller owns, or 404.

    `allow_deleted=True` is the readable variant, for `GET /{id}`: the caller's
    soft-deleted row is returned. The default is the writable variant, for PATCH
    and DELETE: the caller's soft-deleted row is a 409 (`DeletedRow`). Deleting an
    already-deleted row is refused rather than a silent no-op because a second
    `deleted_at` write would break Restore's match (docs/adr/0014).

    One implementation for every entity. The lookup and the 404 come from
    `ownership.require_owned`, which the create-time payload checks also go
    through, so "owned" cannot come to mean two different things depending on
    whether the id arrived in the path or in the body.

    The signature is constructed rather than written out because FastAPI matches
    a dependency's parameter *name* against the `{budget_id}` in the route path;
    a shared `obj_id` parameter would silently rename every route's path
    variable and its OpenAPI entry.
    """

    async def dependency(user: CurrentUser, db: DbSession, **path: uuid.UUID) -> T:
        return await require_owned(
            db, model, path[param_name], user, allow_deleted=allow_deleted
        )

    dependency.__signature__ = Signature(  # type: ignore[attr-defined]
        [
            Parameter(
                param_name, Parameter.POSITIONAL_OR_KEYWORD, annotation=uuid.UUID
            ),
            Parameter("user", Parameter.POSITIONAL_OR_KEYWORD, annotation=CurrentUser),
            Parameter("db", Parameter.POSITIONAL_OR_KEYWORD, annotation=DbSession),
        ]
    )
    return dependency


# Readable: the caller's row, deleted or not. For GET routes only.
ReadableBudget = Annotated[
    Budget, Depends(owned_path(Budget, "budget_id", allow_deleted=True))
]
ReadableExpense = Annotated[
    Expense, Depends(owned_path(Expense, "expense_id", allow_deleted=True))
]
ReadableTransaction = Annotated[
    Transaction,
    Depends(owned_path(Transaction, "transaction_id", allow_deleted=True)),
]
ReadableCategory = Annotated[
    Category, Depends(owned_path(Category, "category_id", allow_deleted=True))
]

# Writable: the caller's live row; a deleted one is a 409. For PATCH and DELETE.
WritableBudget = Annotated[Budget, Depends(owned_path(Budget, "budget_id"))]
WritableExpense = Annotated[Expense, Depends(owned_path(Expense, "expense_id"))]
WritableTransaction = Annotated[
    Transaction, Depends(owned_path(Transaction, "transaction_id"))
]
WritableCategory = Annotated[Category, Depends(owned_path(Category, "category_id"))]
