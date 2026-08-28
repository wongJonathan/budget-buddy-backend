from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models.user import User
from app.security.sessions import SessionToken, resolve_session

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    session: SessionToken,
) -> User:
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    user = await resolve_session(db, session)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
