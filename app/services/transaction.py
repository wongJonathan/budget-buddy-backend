import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.schemas.transaction import TransactionCreate, TransactionUpdate


async def create_transaction(db: AsyncSession, data: TransactionCreate) -> Transaction:
    transaction = Transaction(**data.model_dump())
    db.add(transaction)
    await db.commit()
    await db.refresh(transaction)
    return transaction


async def get_transaction(db: AsyncSession, transaction_id: uuid.UUID) -> Transaction | None:
    return await db.get(Transaction, transaction_id)


async def list_transactions(db: AsyncSession) -> Sequence[Transaction]:
    result = await db.execute(select(Transaction))
    return result.scalars().all()


async def update_transaction(
    db: AsyncSession, transaction_id: uuid.UUID, data: TransactionUpdate
) -> Transaction | None:
    transaction = await db.get(Transaction, transaction_id)
    if transaction is None:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(transaction, field, value)
    await db.commit()
    await db.refresh(transaction)
    return transaction


async def delete_transaction(db: AsyncSession, transaction_id: uuid.UUID) -> bool:
    transaction = await db.get(Transaction, transaction_id)
    if transaction is None:
        return False
    await db.delete(transaction)
    await db.commit()
    return True
