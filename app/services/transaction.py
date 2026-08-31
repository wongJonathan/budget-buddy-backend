import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.schemas.transaction import TransactionCreate, TransactionUpdate
from app.services.visibility import live_transactions


async def create_transaction(db: AsyncSession, data: TransactionCreate) -> Transaction:
    transaction = Transaction(**data.model_dump())
    db.add(transaction)
    await db.commit()
    await db.refresh(transaction)
    return transaction


async def get_transaction(db: AsyncSession, transaction_id: uuid.UUID) -> Transaction | None:
    result = await db.scalars(live_transactions().where(Transaction.id == transaction_id))
    return result.one_or_none()


async def list_transactions(db: AsyncSession) -> Sequence[Transaction]:
    result = await db.execute(live_transactions())
    return result.scalars().all()


async def update_transaction(
    db: AsyncSession, transaction_id: uuid.UUID, data: TransactionUpdate
) -> Transaction | None:
    transaction = await get_transaction(db, transaction_id)
    if transaction is None:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(transaction, field, value)
    await db.commit()
    await db.refresh(transaction)
    return transaction


async def soft_delete_transaction(db: AsyncSession, transaction_id: uuid.UUID) -> bool:
    transaction = await get_transaction(db, transaction_id)
    if transaction is None:
        return False
    transaction.is_deleted = True
    await db.commit()
    return True
