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


def owned_path[T](model: type[T], param_name: str) -> Callable[..., Awaitable[T]]:
    """Resolve a path parameter to a row the caller owns, or 404.

    One implementation for all three entities. The lookup and the 404 come from
    `ownership.require_owned`, which the create-time payload checks also go
    through, so "owned" cannot come to mean two different things depending on
    whether the id arrived in the path or in the body.

    The signature is constructed rather than written out because FastAPI matches
    a dependency's parameter *name* against the `{budget_id}` in the route path;
    a shared `obj_id` parameter would silently rename every route's path
    variable and its OpenAPI entry.
    """

    async def dependency(user: CurrentUser, db: DbSession, **path: uuid.UUID) -> T:
        return await require_owned(db, model, path[param_name], user)

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


OwnedBudget = Annotated[Budget, Depends(owned_path(Budget, "budget_id"))]
OwnedExpense = Annotated[Expense, Depends(owned_path(Expense, "expense_id"))]
OwnedTransaction = Annotated[
    Transaction, Depends(owned_path(Transaction, "transaction_id"))
]
OwnedCategory = Annotated[Category, Depends(owned_path(Category, "category_id"))]
