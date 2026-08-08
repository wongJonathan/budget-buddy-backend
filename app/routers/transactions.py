import uuid
from collections.abc import Sequence

from fastapi import APIRouter, HTTPException, status

from app.dependencies import DbSession
from app.models.transaction import Transaction
from app.schemas.transaction import TransactionCreate, TransactionRead, TransactionUpdate
from app.services import transaction as transaction_service

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.post("", response_model=TransactionRead, status_code=status.HTTP_201_CREATED)
async def create_transaction(data: TransactionCreate, db: DbSession) -> Transaction:
    return await transaction_service.create_transaction(db, data)


@router.get("", response_model=list[TransactionRead])
async def list_transactions(db: DbSession) -> Sequence[Transaction]:
    return await transaction_service.list_transactions(db)


@router.get("/{transaction_id}", response_model=TransactionRead)
async def get_transaction(transaction_id: uuid.UUID, db: DbSession) -> Transaction:
    transaction = await transaction_service.get_transaction(db, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
    return transaction


@router.patch("/{transaction_id}", response_model=TransactionRead)
async def update_transaction(
    transaction_id: uuid.UUID, data: TransactionUpdate, db: DbSession
) -> Transaction:
    transaction = await transaction_service.update_transaction(db, transaction_id, data)
    if transaction is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
    return transaction


@router.delete("/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transaction(transaction_id: uuid.UUID, db: DbSession) -> None:
    deleted = await transaction_service.delete_transaction(db, transaction_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
