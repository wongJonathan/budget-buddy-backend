from fastapi import APIRouter, HTTPException, status

from app.dependencies import CurrentUser, DbSession
from app.models.user import User
from app.schemas.budget import BudgetRead
from app.schemas.user import UserRead, UserUpdate, UserWithBudgetsRead
from app.services import budget as budget_service
from app.services import user as user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=UserWithBudgetsRead)
async def get_user(user: CurrentUser, db: DbSession) -> UserWithBudgetsRead:

    budgets = await budget_service.list_budgets(db, user.id)
    budget_reads = [BudgetRead.model_validate(budget) for budget in budgets]

    return UserWithBudgetsRead(
        **UserRead.model_validate(user).model_dump(),
        budgets=budget_reads,
    )


@router.patch("", response_model=UserRead)
async def update_user(user: CurrentUser, data: UserUpdate, db: DbSession) -> User:
    updated_user = await user_service.update_user(db, user, data)

    if updated_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    return updated_user


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user: CurrentUser, db: DbSession) -> None:
    deleted = await user_service.delete_user(db, user)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )


@router.patch("/last-active", response_model=UserRead)
async def update_last_active(user: CurrentUser, db: DbSession) -> User:
    last_active_user = await user_service.update_user_last_active(db, user)
    if last_active_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    return last_active_user
