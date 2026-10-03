from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request, Response, status

from app.dependencies import CurrentUser, DbSession, SessionToken
from app.schemas.auth import LoginRequest
from app.security.cookies import clear_session_cookie, set_session_cookie
from app.security.passwords import hash_password, verify_and_update_password
from app.security.sessions import create_session, revoke_sesion
from app.services import user as user_service

router = APIRouter(prefix="/auth", tags=["auth"])

# The IANA zone the device reports, e.g. from `Intl.DateTimeFormat().resolvedOptions()`.
# Optional: an older client that doesn't send it keeps the stored zone.
TimeZoneHeader = Annotated[str | None, Header(alias="X-Time-Zone")]


@router.post("/login")
async def login(
    data: LoginRequest,
    response: Response,
    request: Request,
    db: DbSession,
    time_zone: TimeZoneHeader = None,
) -> dict[str, Any]:
    email = data.email
    password = data.password.get_secret_value()

    user = await user_service.get_user_by_email(db, email)
    if not user:
        # Used to mask not found user
        verify_and_update_password(password, hash_password("DUMMY HASH"))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)

    is_verified, _ = verify_and_update_password(password, user.hashed_password)
    if not is_verified:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)

    await user_service.record_activity(db, user, time_zone)

    # TODO: When an update is available should update the db's stored hash
    token = await create_session(db, user, request.headers.get("user-agent", ""))
    set_session_cookie(response, token)

    return {"id": user.id, "email": user.email}


@router.post("/logout")
async def logout(response: Response, db: DbSession, session: SessionToken) -> None:
    if session:
        await revoke_sesion(db, session)

    clear_session_cookie(response)


@router.get("/me")
async def me(user: CurrentUser, db: DbSession, time_zone: TimeZoneHeader = None) -> dict[str, Any]:
    await user_service.record_activity(db, user, time_zone)
    return {"id": user.id, "email": user.email}


@router.post("/change-password")
async def change_password(user: CurrentUser) -> None:
    pass
