import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.models.user import User
from app.ownership import verify_owned_refs
from app.schemas.transaction import TransactionCreate, TransactionUpdate
from app.services.visibility import live_transactions


async def create_transaction(db: AsyncSession, user: User, data: TransactionCreate) -> Transaction:
    await verify_owned_refs(db, data, user)
    transaction = Transaction(**data.model_dump(), user_id=user.id)
    db.add(transaction)
    await db.commit()
    await db.refresh(transaction)
    return transaction


async def get_transaction(
    db: AsyncSession, transaction_id: uuid.UUID, user_id: uuid.UUID
) -> Transaction | None:
    result = await db.scalars(
        live_transactions().where(Transaction.id == transaction_id, Transaction.user_id == user_id)
    )
    return result.one_or_none()


async def list_transactions(
    db: AsyncSession,
    user: User,
) -> Sequence[Transaction]:
    result = await db.execute(live_transactions().where(Transaction.user_id == user.id))
    return result.scalars().all()


async def update_transaction(
    db: AsyncSession, transaction: Transaction, data: TransactionUpdate, user: User
) -> Transaction:
    await verify_owned_refs(db, data, user)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(transaction, field, value)
    await db.commit()
    await db.refresh(transaction)
    return transaction


async def soft_delete_transaction(db: AsyncSession, transaction: Transaction) -> bool:
    transaction.is_deleted = True
    await db.commit()
    return True
