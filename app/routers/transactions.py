from collections.abc import Sequence

from fastapi import APIRouter, status

from app.dependencies import CurrentUser, DbSession, OwnedTransaction
from app.models.transaction import Transaction
from app.schemas.transaction import (
    TransactionCreate,
    TransactionRead,
    TransactionUpdate,
)
from app.services import transaction as transaction_service

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.post("", response_model=TransactionRead, status_code=status.HTTP_201_CREATED)
async def create_transaction(
    user: CurrentUser, data: TransactionCreate, db: DbSession
) -> Transaction:
    return await transaction_service.create_transaction(db, user, data)


@router.get("", response_model=list[TransactionRead])
async def list_transactions(user: CurrentUser, db: DbSession) -> Sequence[Transaction]:
    return await transaction_service.list_transactions(db, user)


@router.get("/{transaction_id}", response_model=TransactionRead)
async def get_transaction(transaction: OwnedTransaction) -> Transaction:
    return transaction


@router.patch("/{transaction_id}", response_model=TransactionRead)
async def update_transaction(
    transaction: OwnedTransaction, data: TransactionUpdate, user: CurrentUser, db: DbSession
) -> Transaction:
    return await transaction_service.update_transaction(db, transaction, data, user)


@router.delete("/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transaction(transaction: OwnedTransaction, db: DbSession) -> None:
    await transaction_service.soft_delete_transaction(db, transaction)
