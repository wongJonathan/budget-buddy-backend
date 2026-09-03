"""Writing to the ledger.

One client intention can become more than one row. A spend against an Expense with a
funded fund draws from the fund first and the remainder from the Pool, which is a
`SPEND_SAVED` row plus a `SPEND` row - so `create_transaction` returns a list, for every
type, rather than making the caller guess when the shape changes.

The split is the server's to compute, never the client's to declare: it depends on a fund
balance the client cannot see, and a client-supplied `spend_saved` could overdraw. The
two rows it writes are deliberately *not* linked to each other. Once written they
describe two genuinely distinct movements of money, both balance sums are row-by-row, and
the "one purchase" they came from is a UI concept rather than a ledger one - the same
reasoning that makes Income a bare Transaction. See docs/adr/0011.
"""

import uuid
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import TransactionType
from app.models.expense import Expense
from app.models.transaction import Transaction
from app.models.user import User
from app.ownership import require_owned, verify_owned_refs
from app.schemas.transaction import TransactionCreate, TransactionUpdate
from app.services import savings as savings_service
from app.services.visibility import live_transactions


async def create_transaction(
    db: AsyncSession, user: User, data: TransactionCreate
) -> list[Transaction]:
    """Write the rows one client intention implies, and return all of them."""
    await verify_owned_refs(db, data, user)

    # Re-fetched rather than taken from the check above, which validates ownership
    # without handing the row back. One extra query on the expense-linked path, in
    # exchange for `verify_owned_refs` staying the single declarative ownership rule.
    expense = (
        await require_owned(db, Expense, data.expense_id, user)
        if data.expense_id is not None
        else None
    )

    if expense is not None and data.type is TransactionType.SAVE:
        # A fund exists because money went into it, never because an Expense was created.
        savings = await savings_service.open_savings(db, expense, user)
        rows = [Transaction(**data.model_dump(), user_id=user.id, savings_id=savings.id)]
    elif expense is not None and data.type is TransactionType.SPEND:
        rows = await _split_spend(db, user, data, expense)
    else:
        rows = [Transaction(**data.model_dump(), user_id=user.id)]

    db.add_all(rows)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return rows


async def _split_spend(
    db: AsyncSession, user: User, data: TransactionCreate, expense: Expense
) -> list[Transaction]:
    """Draw from the fund first, then the Pool.

    Unconditional, with no opt-out: money set aside for an Expense has no other purpose,
    so "spend on this but leave its savings alone" is a request to overstate the month's
    spending rather than a real intention. Keeping it unconditional also makes the split
    a pure function of `(expense, amount)`, and therefore reproducible.

    A spend the fund covers entirely produces one `SPEND_SAVED` row and no `SPEND` row -
    which is why the Expense's Met can move by less than the amount spent.
    """
    from_pot = min(await savings_service.spendable(db, expense), data.amount)
    if from_pot <= 0:
        return [Transaction(**data.model_dump(), user_id=user.id)]

    fields = data.model_dump()
    rows = [
        Transaction(
            **{**fields, "type": TransactionType.SPEND_SAVED, "amount": from_pot},
            user_id=user.id,
            # Stamped on the fund half only. The Pool half below carries no `savings_id`
            # because it moved no fund - the invariant the balance sum relies on.
            savings_id=expense.savings_id,
        )
    ]
    remainder = data.amount - from_pot
    if remainder > Decimal(0):
        rows.append(Transaction(**{**fields, "amount": remainder}, user_id=user.id))
    return rows


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


async def soft_delete_transaction(db: AsyncSession, transaction: Transaction) -> None:
    transaction.is_deleted = True
    await db.commit()
