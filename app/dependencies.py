import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models.budget import Budget
from app.models.user import User
from app.security.sessions import SessionToken, resolve_session
from app.services import budget as budget_service

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


async def get_users_budget(
    budget_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> Budget:
    budget = await budget_service.get_budget(db, budget_id, user.id)

    if budget is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Budget not found")
    return budget


OwnedBudget = Annotated[Budget, Depends(get_users_budget)]
