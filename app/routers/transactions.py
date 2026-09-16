import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.dependencies import CurrentUser, DbSession, OwnedTransaction
from app.models.transaction import Transaction
from app.schemas.fields import current_period, last_of_month
from app.schemas.transaction import (
    TransactionCreate,
    TransactionPage,
    TransactionRead,
    TransactionUpdate,
)
from app.services import transaction as transaction_service

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.post(
    "", response_model=list[TransactionRead], status_code=status.HTTP_201_CREATED
)
async def create_transaction(
    user: CurrentUser, data: TransactionCreate, db: DbSession
) -> list[Transaction]:
    """
    Handles creating all the transaction logic where the caller does a simple save, spend, transfer
    and depending on the type of transaction it can create multiple transaction rows (spend_saved
    and spend if a savings amount is fully used)
    """
    return await transaction_service.create_transaction(db, user, data)


@router.get("", response_model=TransactionPage)
async def list_transactions(
    user: CurrentUser,
    db: DbSession,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TransactionPage:
    items, total = await transaction_service.list_transactions(
        db,
        user,
        date_from=date_from or current_period(),
        date_to=date_to or last_of_month(current_period()),
        limit=limit,
        offset=offset,
    )
    return TransactionPage(
        items=[TransactionRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{transaction_id}", response_model=TransactionRead)
async def get_transaction(transaction: OwnedTransaction) -> Transaction:
    return transaction


@router.patch("/{transaction_id}", response_model=TransactionRead)
async def update_transaction(
    transaction: OwnedTransaction,
    data: TransactionUpdate,
    user: CurrentUser,
    db: DbSession,
) -> Transaction:
    return await transaction_service.update_transaction(db, transaction, data, user)


@router.delete("/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transaction(transaction: OwnedTransaction, db: DbSession) -> None:
    await transaction_service.soft_delete_transaction(db, transaction)
