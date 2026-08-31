import uuid
from collections.abc import Sequence

from fastapi import APIRouter, HTTPException, status

from app.dependencies import DbSession
from app.models.user import User
from app.schemas.budget import BudgetRead
from app.schemas.user import UserRead, UserUpdate, UserWithBudgetsRead
from app.services import budget as budget_service
from app.services import user as user_service

router = APIRouter(prefix="/users", tags=["users"])


# No POST: accounts are provisioned by app.scripts.create_user, never over HTTP.
# See docs/adr/0006-accounts-provisioned-by-script.md.


@router.get("", response_model=list[UserRead])
async def list_users(db: DbSession) -> Sequence[User]:
    return await user_service.list_users(db)


@router.get("/{user_id}", response_model=UserWithBudgetsRead)
async def get_user(user_id: uuid.UUID, db: DbSession) -> UserWithBudgetsRead:
    user = await user_service.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    budgets = await budget_service.list_budgets(db, user.id)
    budget_reads = [BudgetRead.model_validate(budget) for budget in budgets]

    return UserWithBudgetsRead(
        **UserRead.model_validate(user).model_dump(),
        budgets=budget_reads,
    )


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(user_id: uuid.UUID, data: UserUpdate, db: DbSession) -> User:
    user = await user_service.update_user(db, user_id, data)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: uuid.UUID, db: DbSession) -> None:
    deleted = await user_service.delete_user(db, user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")


@router.patch("/{user_id}/last-active", response_model=UserRead)
async def update_last_active(user_id: uuid.UUID, db: DbSession) -> User:
    user = await user_service.update_user_last_active(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user
