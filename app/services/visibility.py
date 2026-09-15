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
`GET /budgets/{id}/expenses?include_deleted=true`); surfacing anything belonging to a deleted
Budget is not, because the parent being gone is what "deleted" means for the child.

Category takes no part in this: it hard-deletes, guarded by ON DELETE RESTRICT.

**Transaction takes no part in the ancestor rule.** It applies between Budget and
Expense only. A Transaction is visible whenever it is not itself deleted, whatever
happened to the Expense it references.

That is a consequence of ADR-0009 rather than an exception to ADR-0007. The ancestor
rule was written when a Transaction was "an actual measured against a plan", where
hiding it along with its plan is coherent. ADR-0009 redefined a Transaction as any
change to the money available to a User - a fact about money, not about a plan - and a
record that GBP 300 left the account does not stop being true because the plan it was
measured against was deleted. Deleting an Expense removes the plan; the ledger stands.

It is also what keeps money correct. Any sum over money - the Pool, a Savings balance -
counts Transactions, and an ancestor rule would have unspent every Spend under a deleted
Expense, quietly inflating the Pool by the amount the user had spent. See ADR-0007 as
amended.
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
    """Transactions that haven't been deleted.

    No join, and deliberately so: a Transaction has no ancestor for visibility purposes.
    It is a movement of the User's money, and it stays on the record whether or not the
    Expense it references is still there - see the module docstring for why that changed
    with ADR-0009, and ADR-0007 as amended.

    This is what a caller wanting "everything about this user's money" gets, which is
    why the Pool and `savings.balance` can both build on it rather than each writing
    their own filter and drifting apart.
    """
    query = select(Transaction)
    if not include_deleted:
        query = query.where(~Transaction.is_deleted)
    return query
