"""Ownership of client-supplied foreign keys.

A create or update payload names its parents by id, and those ids come from the
client. Stamping `user_id` on the new row is therefore not what makes it the
caller's - the parent it points at has to be theirs too. Without that check an
authenticated user can plant a row inside someone else's budget, and it shows up
in *their* budget view, because `list_budget_expenses` filters on `budget_id`.

The requirement is declared on the schema field rather than written out in each
service:

    class ExpenseCreate(BaseModel):
        budget_id: BudgetRef
        category_id: CategoryRef

and every write path makes one call - `verify_owned_refs(db, data, user)` - which
walks the payload and checks each declared reference. Adding a foreign key to a
schema is therefore enough to get it checked; there is no second place to
remember. `tests/test_ownership.py` fails the build if an `*_id` field on an
input schema is left unannotated and unexempted.

Lookups go through `visibility.live_*`, so a soft-deleted parent is "not yours"
for the same reason a stranger's is. Both raise `NotOwned`, which the handler in
`exceptions.py` renders as a 404 - never a 403, which would confirm the row
exists.

See `docs/adr/0008-ownership-declared-on-schema-fields.md` for why the
requirement is declared on the field rather than written out per service, and
why the database-level version of this was deferred rather than rejected.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.exceptions import AppError
from app.models.budget import Budget
from app.models.category import Category
from app.models.expense import Expense
from app.models.transaction import Transaction
from app.models.user import User
from app.services.visibility import live_budgets, live_expenses, live_transactions


class NotOwned(AppError):
    """The caller asked to attach a row to a parent that isn't theirs.

    404, not 403: a budget belonging to someone else has to be indistinguishable
    from one that was never there.
    """

    def __init__(self, model: type[Any]) -> None:
        super().__init__(f"{model.__name__} not found", status_code=404)


@dataclass(frozen=True)
class Owned:
    """Marker: this id must name a row the caller owns."""

    model: type[Any]


# The four aliases the schemas annotate with. Written out rather than a generic
# `OwnedRef[Budget]` because a PEP 695 alias does not substitute its type
# parameter inside Annotated metadata - `Owned(T)` would stay a TypeVar and the
# marker would silently vanish.
BudgetRef = Annotated[uuid.UUID, Owned(Budget)]
CategoryRef = Annotated[uuid.UUID, Owned(Category)]
ExpenseRef = Annotated[uuid.UUID, Owned(Expense)]
TransactionRef = Annotated[uuid.UUID, Owned(Transaction)]


@dataclass(frozen=True)
class _Ownable:
    """How to find one entity's rows, and which columns decide identity and owner."""

    live: Callable[[], Select[Any]]
    id_column: InstrumentedAttribute[uuid.UUID]
    owner_column: InstrumentedAttribute[uuid.UUID]


# The registry every ownership check reads. One entry per ownable entity, and
# adding an entity is this line plus its `*Ref` alias above. Category is a bare
# select: it hard-deletes, so it has no live_* of its own.
_OWNABLE: dict[type[Any], _Ownable] = {
    Budget: _Ownable(live_budgets, Budget.id, Budget.user_id),
    Category: _Ownable(lambda: select(Category), Category.id, Category.user_id),
    Expense: _Ownable(live_expenses, Expense.id, Expense.user_id),
    Transaction: _Ownable(live_transactions, Transaction.id, Transaction.user_id),
}


async def require_owned[T](
    db: AsyncSession, model: type[T], obj_id: uuid.UUID, user: User
) -> T:
    """Return the caller's row of `model`, or raise `NotOwned`.

    The single lookup behind both ownership checks: the path-parameter
    dependencies in `dependencies.py` and the payload walk below.
    """
    ownable = _OWNABLE[model]
    query = ownable.live().where(ownable.id_column == obj_id, ownable.owner_column == user.id)  # type: ignore
    row = (await db.scalars(query)).one_or_none()
    if row is None:
        raise NotOwned(model)
    return row  # type: ignore[no-any-return]


def _owned_marker(field: FieldInfo) -> Owned | None:
    """The `Owned(...)` on a field, wherever pydantic parked it.

    A plain `BudgetRef` is hoisted onto `field.metadata`; an optional
    `BudgetRef | None` keeps it nested inside the union instead, so both places
    have to be looked at.
    """
    for meta in field.metadata:
        if isinstance(meta, Owned):
            return meta
    return _find_owned(field.annotation)


def _find_owned(annotation: Any) -> Owned | None:
    for meta in getattr(annotation, "__metadata__", ()):
        if isinstance(meta, Owned):
            return meta
    for arg in getattr(annotation, "__args__", ()):
        found = _find_owned(arg)
        if found is not None:
            return found
    return None


async def verify_owned_refs(db: AsyncSession, data: BaseModel, user: User) -> None:
    """Check every owned reference the payload actually set.

    Unset optional fields are skipped - `exclude_unset` is what separates "the
    client didn't mention transfer_id" from "the client set it to null".
    """
    supplied = data.model_dump(exclude_unset=True)
    for name, field in type(data).model_fields.items():
        marker = _owned_marker(field)
        if marker is None or name not in supplied:
            continue
        value = supplied[name]
        if value is None:
            continue
        await require_owned(db, marker.model, value, user)
