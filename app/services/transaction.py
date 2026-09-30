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

import datetime
import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.exceptions import AppError
from app.models.enums import TransactionType
from app.models.expense import Expense
from app.models.savings import Savings
from app.models.transaction import Transaction
from app.models.user import User
from app.ownership import require_owned, verify_owned_refs
from app.schemas.fields import current_period
from app.schemas.transaction import (
    TransactionCreate,
    TransactionGroup,
    TransactionUpdate,
)
from app.services import savings as savings_service
from app.services.savings import FundOverdrawn
from app.services.visibility import live_transactions


async def create_transaction(
    db: AsyncSession, user: User, data: TransactionCreate
) -> list[Transaction]:
    """Write the rows one client intention implies, and return all of them."""
    rows = await _write(db, user, data)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return rows


class BulkItemFailed(AppError):
    def __init__(self, index: int, cause: AppError) -> None:
        self.index = index
        super().__init__(
            f"item {index}: {cause.message}", status_code=cause.status_code
        )


async def create_transactions(
    db: AsyncSession, user: User, items: Sequence[TransactionCreate]
) -> list[Transaction]:
    rows: list[Transaction] = []
    for index, data in enumerate(items):
        try:
            rows += await _write(db, user, data)
        except AppError as e:
            await db.rollback()
            raise BulkItemFailed(index, e) from e
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return rows


async def _write(
    db: AsyncSession, user: User, data: TransactionCreate
) -> list[Transaction]:
    """Add and flush the rows for `data`, uncommitted, so the caller owns the commit."""
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
        await savings_service.open_savings(db, expense, user)
        rows = [Transaction(**_row_fields(data), user_id=user.id)]
    elif expense is not None and data.type is TransactionType.SPEND:
        rows = await _split_spend(db, user, data, expense)
    elif expense is not None and data.type is TransactionType.TRANSFER:
        rows = await _create_transfer(db, user, data, expense)
    else:
        rows = [Transaction(**_row_fields(data), user_id=user.id)]

    db.add_all(rows)
    # Explicit rather than left to autoflush: the next bulk item's balance read must
    # see these rows.
    await db.flush()
    return rows


def _row_fields(data: TransactionCreate) -> dict[str, Any]:
    """The payload as Transaction columns. `to_expense_id` names a second row to write,
    not a column of this one."""
    return data.model_dump(exclude={"to_expense_id"})


class InvalidTransfer(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=422)


class TransferLocked(AppError):
    """An Expense deletion has taken part of the group, or the group is its drain pair.

    Editing what is left would move money in or out of a closed fund, or leave Restore
    bringing back a row that no longer matches. Restore the Expense first.
    """

    def __init__(self) -> None:
        super().__init__(
            "this transfer involves a deleted expense; restore it before changing the transfer",
            status_code=409,
        )


def _in_transfer(row: Transaction) -> bool:
    """Whether `row` belongs to a Transfer group rather than to its Expense."""
    return row.transfer_id is not None


async def _load_group(db: AsyncSession, row: Transaction) -> TransactionGroup:
    members = (
        await db.scalars(
            live_transactions(include_deleted=True).where(
                Transaction.transfer_id == row.transfer_id
            )
        )
    ).all()
    for member in members:
        if member.expense_id is None:
            continue
        expense = await db.get(Expense, member.expense_id)
        if expense is None or expense.deleted_at is None:
            continue
        if member.deleted_at is None or member.deleted_at == expense.deleted_at:
            raise TransferLocked()

    live = [t for t in members if t.deleted_at is None]
    anchor = next(t for t in live if t.id == t.transfer_id)
    pool_side = next(t for t in live if t.expense_id is None)
    save = next((t for t in live if t.type is TransactionType.SAVE), None)
    return TransactionGroup(anchor, pool_side, save)


async def _endpoint(
    db: AsyncSession, user: User, expense_id: uuid.UUID, role: str
) -> Expense:
    """A source or destination: the caller's, live, and in the open Period.

    The Period rule is what lets deleting and Restoring an Expense find everything that
    touched its fund this Period by `expense_id` alone.
    """
    expense = await require_owned(db, Expense, expense_id, user)
    if expense.period != current_period():
        raise InvalidTransfer(f"a transfer's {role} must be in the current Period")
    return expense


async def _draw_limit(db: AsyncSession, source: Expense, give_back: Decimal) -> Decimal:
    """What `source` may send, holding its fund's row lock until the commit.

    The lock is the whole guard: the balance is a sum the database cannot constrain, so
    two requests reading it at once would both see the money and both move it.
    `give_back` is what this Transfer already took from the fund, on an edit.
    """
    savings = None
    if source.savings_id is not None:
        savings = (
            await db.scalars(
                select(Savings).where(Savings.id == source.savings_id).with_for_update()
            )
        ).one_or_none()
    if savings is None or savings.deleted_at is not None:
        raise InvalidTransfer(f"'{source.name}' has no savings to transfer from")
    return await savings_service.balance(db, savings.id) + give_back


async def _open_destination(
    db: AsyncSession, user: User, destination: Expense, source: Expense
) -> None:
    if destination.id == source.id:
        raise InvalidTransfer("a transfer cannot go to the same Expense it comes from")
    if (
        destination.savings_id is not None
        and destination.savings_id == source.savings_id
    ):
        raise InvalidTransfer("the source and destination share one savings fund")
    await savings_service.open_savings(db, destination, user)


def _check_amount(amount: Decimal, held: Decimal, source: Expense) -> None:
    if amount > held:
        raise InvalidTransfer(
            f"'{source.name}' holds {held}; it cannot transfer {amount}"
        )


async def _create_transfer(
    db: AsyncSession, user: User, data: TransactionCreate, source: Expense
) -> list[Transaction]:
    """Write the group: anchor, Pool side, and a Save if there is a destination."""
    source = await _endpoint(db, user, source.id, "source")
    _check_amount(data.amount, await _draw_limit(db, source, Decimal(0)), source)
    destination = None
    if data.to_expense_id is not None:
        destination = await _endpoint(db, user, data.to_expense_id, "destination")
        await _open_destination(db, user, destination, source)

    fields = data.model_dump(exclude={"expense_id", "to_expense_id", "type", "name"})
    fields.update(user_id=user.id)
    # Because transfer means movement between funds (fund to pool).
    # Having it be spend save would have it count against monthly spent
    anchor = Transaction(
        **fields, type=TransactionType.TRANSFER, expense_id=source.id, name=data.name
    )
    db.add(anchor)
    # Flushed for its id, which the database generates; the anchor then points at
    # itself, like every other row of the group.
    await db.flush()
    anchor.transfer_id = anchor.id
    rows = [
        anchor,
        Transaction(
            **fields,
            type=TransactionType.TRANSFER,
            expense_id=None,
            transfer_id=anchor.id,
            name="Transfer to total",
        ),
    ]
    if destination is not None:
        rows.append(
            Transaction(
                **fields,
                type=TransactionType.SAVE,
                expense_id=destination.id,
                transfer_id=anchor.id,
                name=destination.name,
            )
        )
    return rows


async def _update_transfer(
    db: AsyncSession, row: Transaction, data: TransactionUpdate, user: User
) -> Transaction:
    """Edit the group `row` belongs to, whichever of its rows was PATCHed.

    Every edit ends in the same check: no fund this group touches may be left lower
    and below zero. Shrinking, re-pointing or dropping the Save takes money back out of
    the destination, which may already have spent it.
    """
    group = await _load_group(db, row)
    changes = data.model_dump(exclude_unset=True)

    if "type" in changes and changes["type"] is not row.type:
        raise InvalidTransfer(
            "a transfer's rows cannot change type; delete the transfer and record what "
            "you meant instead"
        )

    if group.anchor.expense_id is None:
        raise RuntimeError(f"transfer {group.anchor.id} has no source expense")
    old_source = await require_owned(db, Expense, group.anchor.expense_id, user)
    source = old_source
    if "expense_id" in changes:
        if changes["expense_id"] is None:
            raise InvalidTransfer("a transfer needs a source")
        if changes["expense_id"] != old_source.id:
            source = await _endpoint(db, user, changes["expense_id"], "source")

    old_destination_id = group.save.expense_id if group.save else None
    destination_id = changes.get("to_expense_id", old_destination_id)
    destination = None
    if destination_id is not None:
        destination = (
            await _endpoint(db, user, destination_id, "destination")
            if destination_id != old_destination_id
            else await require_owned(db, Expense, destination_id, user)
        )

    old_destination = (
        await db.get(Expense, old_destination_id) if old_destination_id else None
    )
    before = await savings_service.balances(
        db,
        [
            old_source.savings_id,
            source.savings_id,
            destination.savings_id if destination else None,
            old_destination.savings_id if old_destination else None,
        ],
    )

    amount: Decimal = changes.get("amount", group.anchor.amount)
    if amount <= 0:
        raise InvalidTransfer("a transfer amount must be positive")
    same_fund = source.savings_id == old_source.savings_id
    held = await _draw_limit(
        db, source, group.anchor.amount if same_fund else Decimal(0)
    )
    _check_amount(amount, held, source)
    if destination is not None:
        await _open_destination(db, user, destination, source)

    shared: dict[str, Any] = {
        k: changes[k] for k in ("name", "note", "date") if k in changes
    }
    shared["amount"] = amount
    for member in group.rows:
        for field, value in shared.items():
            setattr(member, field, value)
    group.anchor.expense_id = source.id

    if destination is None and group.save is not None:
        group.save.deleted_at = func.now()
    elif destination is not None and group.save is not None:
        group.save.expense_id = destination.id
    elif destination is not None:
        db.add(
            Transaction(
                type=TransactionType.SAVE,
                expense_id=destination.id,
                transfer_id=group.anchor.id,
                user_id=user.id,
                name=destination.name,
                note=group.anchor.note,
                date=group.anchor.date,
                amount=amount,
            )
        )

    await db.flush()
    short = await savings_service.shortfall(db, before)
    if short is not None:
        await db.rollback()
        raise FundOverdrawn(
            short,
            "the destination has already spent or moved on the money this would take back",
            status_code=422,
        )
    await db.commit()
    await db.refresh(row)
    return row


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
        return [Transaction(**_row_fields(data), user_id=user.id)]

    fields = _row_fields(data)
    rows = [
        Transaction(
            **{
                **fields,
                "type": TransactionType.SPEND_SAVED,
                "amount": from_pot,
                "note": f"Amount taken from saved.\n{data.note}",
            },
            user_id=user.id,
        )
    ]
    remainder = data.amount - from_pot
    if remainder > Decimal(0):
        rows.append(
            Transaction(
                **{
                    **fields,
                    "amount": remainder,
                    "note": f"Amount effecting spent.\n{data.note}",
                },
                user_id=user.id,
            )
        )
    return rows


async def get_transaction(
    db: AsyncSession, transaction_id: uuid.UUID, user_id: uuid.UUID
) -> Transaction | None:
    result = await db.scalars(
        live_transactions().where(
            Transaction.id == transaction_id, Transaction.user_id == user_id
        )
    )
    return result.one_or_none()


class InvalidDateRange(AppError):
    """A listing asked for a window that ends before it starts.

    422 rather than an empty page: the query is well-formed but cannot match
    anything, and returning [] would be indistinguishable from a user with no
    Transactions in a real window."""

    def __init__(self, date_from: datetime.date, date_to: datetime.date) -> None:
        super().__init__(
            f"date_from ({date_from.isoformat()}) is after date_to "
            f"({date_to.isoformat()}); the range would match nothing",
            status_code=422,
        )


async def list_transactions(
    db: AsyncSession,
    user: User,
    date_from: datetime.date,
    date_to: datetime.date,
    limit: int,
    offset: int,
    category_id: uuid.UUID | None = None,
) -> tuple[Sequence[Transaction], int]:
    if date_from > date_to:
        raise InvalidDateRange(date_from, date_to)

    window = live_transactions().where(
        Transaction.user_id == user.id,
        Transaction.date >= date_from,
        Transaction.date <= date_to,
    )
    if category_id is not None:
        window = window.where(Transaction.category_id == category_id)

    total = await db.scalar(select(func.count()).select_from(window.subquery()))

    page = await db.execute(
        window.order_by(
            Transaction.date.desc(),
            Transaction.created_at.desc(),
            Transaction.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )

    return page.scalars().all(), total or 0


async def update_transaction(
    db: AsyncSession, transaction: Transaction, data: TransactionUpdate, user: User
) -> Transaction:
    await verify_owned_refs(db, data, user)
    if _in_transfer(transaction):
        return await _update_transfer(db, transaction, data, user)

    changes = data.model_dump(exclude_unset=True)
    # Either would write half a Transfer: one row where the group needs two or three.
    if changes.get("type") is TransactionType.TRANSFER:
        raise InvalidTransfer(
            "a row's type cannot be changed into a transfer; create the transfer instead"
        )
    if "to_expense_id" in changes:
        raise InvalidTransfer("to_expense_id is only a transfer's destination")

    for field, value in changes.items():
        setattr(transaction, field, value)

    # `savings_id` follows the Expense, so a SAVE moved onto an Expense with no fund yet
    # would count against the Pool and land in no balance. Open one, as create does.
    if transaction.type is TransactionType.SAVE and transaction.expense_id is not None:
        expense = await require_owned(db, Expense, transaction.expense_id, user)
        await savings_service.open_savings(db, expense, user)
    await db.commit()
    await db.refresh(transaction)
    return transaction


async def soft_delete_transaction(db: AsyncSession, transaction: Transaction) -> None:
    """Delete one row, or the whole Transfer group it belongs to.

    Deleting a group gives the source its money back and takes the destination's Save
    out again - refused if the destination has already spent or moved that money on.
    """
    if not _in_transfer(transaction):
        transaction.deleted_at = func.now()
        await db.commit()
        return

    group = await _load_group(db, transaction)
    destination = (
        await db.get(Expense, group.save.expense_id)
        if group.save and group.save.expense_id
        else None
    )
    before = await savings_service.balances(
        db, [destination.savings_id if destination else None]
    )
    # One `now()` for every row, like any other multi-row delete.
    for member in group.rows:
        member.deleted_at = func.now()
    await db.flush()
    short = await savings_service.shortfall(db, before)
    if short is not None:
        await db.rollback()
        raise FundOverdrawn(
            short,
            "the destination has already spent or moved on the money this transfer gave it",
            status_code=409,
        )
    await db.commit()


async def close_transactions(db: AsyncSession, expense: Expense) -> None:
    """Handles soft-deleting all transactions that relate to a given expense within a period.
    Because we create a new expense per roll-over we shouldnt need to check for period
    """

    transactions = await db.execute(
        live_transactions().where(_withdrawn_with(expense, deleted_at=None))
    )

    for transaction in transactions.scalars().all():
        transaction.deleted_at = func.now()


def _withdrawn_with(
    expense: Expense, *, deleted_at: datetime.datetime | None
) -> ColumnElement[bool]:
    """The rows an Expense's deletion takes, as a filter. See `close_transactions`."""
    anchor = aliased(Transaction)
    outgoing = select(anchor.id).where(
        anchor.expense_id == expense.id, anchor.transfer_id == anchor.id
    )
    save = aliased(Transaction)
    incoming = select(save.transfer_id).where(
        save.expense_id == expense.id,
        save.type == TransactionType.SAVE,
        save.transfer_id.is_not(None),
        (
            save.deleted_at.is_(None)
            if deleted_at is None
            else save.deleted_at == deleted_at
        ),
    )
    return or_(
        Transaction.expense_id == expense.id,
        and_(Transaction.expense_id.is_(None), Transaction.transfer_id.in_(outgoing)),
        Transaction.transfer_id.in_(incoming),
    )


class RestoreBlocked(AppError):
    """Restoring would put a Transfer row back on another deleted Expense.

    Deleting a destination withdraws the Transfer into it, source side included. If
    the source was deleted afterwards its fund was drained without that row, and
    bringing it back would take the same money out of a closed fund a second time.
    """

    def __init__(self, name: str) -> None:
        super().__init__(
            f"a transfer this restores comes from '{name}', which is deleted; restore it first",
            status_code=409,
        )


async def restore_transactions(db: AsyncSession, expense: Expense) -> None:
    """Restores transactions deleted by close_transactions.
    We only restore transactions that were deleted at the same time as the expense
    """
    assert expense.deleted_at is not None
    transactions = await db.execute(
        live_transactions(include_deleted=True).where(
            _withdrawn_with(expense, deleted_at=expense.deleted_at),
            Transaction.deleted_at == expense.deleted_at,
        )
    )
    withdrawn = transactions.scalars().all()
    for row in withdrawn:
        if row.expense_id is None or row.expense_id == expense.id:
            continue
        other = await db.get(Expense, row.expense_id)
        if other is not None and other.deleted_at is not None:
            raise RestoreBlocked(other.name)

    # Soft-delete the transfer from savings
    transfer_transactions = await db.execute(
        live_transactions().where(
            Transaction.created_at == expense.deleted_at,
            Transaction.type == TransactionType.TRANSFER,
            Transaction.user_id == expense.user_id,
        )
    )

    for transaction in withdrawn:
        transaction.deleted_at = None

    for transaction in transfer_transactions.scalars().all():
        transaction.deleted_at = func.now()
