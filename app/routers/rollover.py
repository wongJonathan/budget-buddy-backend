from fastapi import APIRouter, status

from app.dependencies import CurrentUser, DbSession
from app.schemas.fields import current_period
from app.services import rollover as rollover_service

router = APIRouter(prefix="/rollover", tags=["rollover"])


@router.post("", response_model=None, status_code=status.HTTP_200_OK)
async def run_rollover(user: CurrentUser, db: DbSession) -> None:
    return await rollover_service.rollover(db, user, current_period())
