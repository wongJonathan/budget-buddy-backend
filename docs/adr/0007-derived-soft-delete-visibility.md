# Soft-delete visibility is derived from ancestors, not propagated on write

**Status**: accepted

Budget, Expense and Transaction all soft-delete via an `is_deleted` flag; their `DELETE` routes set the flag and the rows stay. A row is **visible only if it and every ancestor is undeleted**, evaluated at read time through joins in `app/services/visibility.py`. Soft-deleting a Budget therefore hides its Expenses and their Transactions without writing to a single one of them.

Every read path builds on `live_budgets()` / `live_expenses()` / `live_transactions()`. No service starts from a bare `select()`, and fetch-by-id goes through the same helpers rather than `db.get()`, so a soft-deleted row 404s on a direct link instead of merely vanishing from lists.

The alternative — flagging descendants at write time — was rejected primarily because of restore. With propagation you cannot tell whether an Expense was deleted on its own or because its Budget was, so undelete becomes a data-archaeology problem, and a soft delete you can't reliably undo has given up the only thing it was for. Deriving visibility keeps restore as a single flag flip. The cost is a two- or three-table join on list queries, which is irrelevant at this application's data volume.

`include_deleted=True` relaxes a row's *own* flag and never an ancestor's. Asking for the deleted Expenses of a live Budget is a real feature (it backs `GET /budgets/{id}?include_deleted=true`); surfacing anything belonging to a deleted Budget is not, because the parent being gone is precisely what "deleted" means for the child.

## Foreign keys: three kinds of reference

Soft delete governs user-facing deletion. The FK `ON DELETE` actions govern hard deletion, which after this change happens in exactly one place: account erasure. They are chosen by asking what kind of reference each one is.

- **Constitutive** — the child is meaningless without the parent. `transactions.expense_id`, `expenses.budget_id`, `budgets.user_id`, `categories.user_id`, `sessions.user_id`. These are **CASCADE**.
- **Incidental** — the child stands on its own; the reference is a pointer. `auth_events.user_id`, `users.active_budget_id`, `transactions.transfer_id`. These are **SET NULL**.
- **Reassignable** — the child is valuable and the user can point it elsewhere. `expenses.category_id`. This is **RESTRICT**: deleting a mere label must not destroy the Expenses using it.

SET NULL was considered for the constitutive edges as a "don't lose data" measure and rejected. All three are NOT NULL, so it would require abandoning an invariant `CONTEXT.md` states, and it preserves *rows* while destroying the relationship that makes them mean anything — an orphaned Transaction is an amount and a date attached to nothing. Protection against user error is soft delete's job, not the FK's.

## Considered Options

- **Write-time propagation** (rejected): cheap reads, simple queries, but restore becomes ambiguous and two flags must be kept in sync.
- **Derived visibility** (chosen): one rule, one place, trivial restore, at the cost of joins on read.
- **Per-query `.where()` clauses** (rejected): what the code did before. Already drifted — `list_expenses` filtered on the Expense flag with no join to Budget, so a deleted Budget's Expenses stayed visible.

## Consequences

Account deletion has a **forced teardown order**, implemented in `user_service.delete_user`. `expenses.category_id` is RESTRICT and checked immediately, so a bare `DELETE FROM users` is refused: the cascade reaches the user's Categories while their Expenses still reference them. Budgets must be deleted first (taking Expenses and Transactions with them) before the Categories are free to go. This was discovered by the test that asserts account deletion purges everything — before it, `DELETE /users` raised a `ForeignKeyViolationError` for any account that had ever recorded an expense.

**`auth_events` is the documented exception to "account deletion purges everything."** It survives with its `email`, IP, user agent and timestamps intact; only `user_id` is nulled. This is deliberate: the audit trail exists to record what happened, and a compromised account that could erase its own trail by deleting itself would defeat it. Anyone reading "we purge all user data" and finding this table should read it as intended, not as a bug.

**Soft-deleted Expenses still occupy their unique slot.** `(budget_id, period, series_id)` is UNIQUE and knows nothing about `is_deleted`, so a soft-deleted Expense will block Rollover from creating its successor for that period. Rollover is not implemented yet, so nothing breaks today — whoever builds it must decide whether to reuse the soft-deleted row or exclude deleted rows from the constraint via a partial unique index.

Nothing reclaims soft-deleted rows. There is no purge job, deliberately: at this data volume deleted rows cost nothing, and a reaper is a way to lose data you meant to keep.
