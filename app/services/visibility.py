"""The soft-delete visibility rule, in one place.

A row is visible only if it *and every ancestor* is undeleted. Deleting a Budget
therefore hides its Expenses and their Transactions without touching those rows -
visibility is derived at read time, never propagated at write time. See
docs/adr/0007-derived-soft-delete-visibility.md for why.

Every read path builds on the selects here rather than writing its own `.where()`, so
the rule can't drift between query and query. A service needing extra filtering adds
`.where(...)` to one of these; it should never start from a bare `select()`.

`include_deleted=True` relaxes the row's *own* flag only - never an ancestor's. Asking
for the deleted Expenses of a live Budget is a real feature (it backs
`GET /budgets/{id}?include_deleted=true`); surfacing anything belonging to a deleted
Budget is not, because the parent being gone is what "deleted" means for the child.

Category takes no part in this: it hard-deletes, guarded by ON DELETE RESTRICT.
"""

from sqlalchemy import Select, select

from app.models.budget import Budget
from app.models.expense import Expense
from app.models.transaction import Transaction


def live_budgets(*, include_deleted: bool = False) -> Select[tuple[Budget]]:
    """Budgets that haven't been deleted.

    A Budget's only ancestor is its User, and Users hard-delete, so there is nothing
    above it to check.
    """
    query = select(Budget)
    if not include_deleted:
        query = query.where(~Budget.is_deleted)
    return query


def live_expenses(*, include_deleted: bool = False) -> Select[tuple[Expense]]:
    """Expenses whose Budget is live, and which aren't themselves deleted."""
    query = select(Expense).join(Budget, Budget.id == Expense.budget_id)
    query = query.where(~Budget.is_deleted)
    if not include_deleted:
        query = query.where(~Expense.is_deleted)
    return query


def live_transactions(*, include_deleted: bool = False) -> Select[tuple[Transaction]]:
    """Transactions whose whole chain up to the Budget is intact."""
    query = (
        select(Transaction)
        .join(Expense, Expense.id == Transaction.expense_id)
        .join(Budget, Budget.id == Expense.budget_id)
        .where(~Expense.is_deleted, ~Budget.is_deleted)
    )
    if not include_deleted:
        query = query.where(~Transaction.is_deleted)
    return query
