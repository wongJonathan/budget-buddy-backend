# Ownership of client-supplied foreign keys is declared on the schema field

**Status**: accepted

Authenticating a request establishes who is asking, not what they may point at. A create payload names its parents by id — `ExpenseCreate.budget_id`, `TransactionCreate.expense_id` — and those ids come from the client, so stamping `user_id = user.id` on the new row does not make the row legitimately theirs. Before this change, an authenticated user could post an expense carrying someone else's `budget_id`: the row was created, marked as belonging to the *sender*, and filed under the *recipient's* budget. It then appeared in that other user's `GET /budgets/{id}` response, because `list_budget_expenses` filters on `budget_id` alone. The same shape applied to `TransactionCreate.expense_id`, to `TransactionUpdate.transfer_id`, and to `UserUpdate.active_budget_id`, which let a user point their active budget at a budget they had never seen.

The parent must therefore be resolved through an owner-scoped lookup rather than trusted. The decision here is not *that* we check — that part is forced — but **where the requirement is written down**.

It is declared on the schema field:

```python
class ExpenseCreate(BaseModel):
    budget_id: BudgetRef
    category_id: CategoryRef
```

and every write path makes a single call, `verify_owned_refs(db, data, user)`, which walks the payload and checks each declared reference against `app/ownership.py`'s registry. Adding a foreign key to an input schema is what causes it to be checked; there is no second place to remember. `tests/test_ownership.py` fails the build if an `*_id` field on a Create/Update schema has neither a `*Ref` annotation nor an entry in `EXEMPT_FIELDS`.

That test is the actual point of the design. The failure mode this class of bug has is not "somebody wrote the check incorrectly" — a wrong check fails loudly the first time it runs. It is "somebody added a field and nobody wrote a check at all", which is silent, indistinguishable from working code, and gets more likely as the schemas grow. Declaring the requirement beside the field is what makes the omission mechanically detectable.

Lookups go through `visibility.live_budgets()` / `live_expenses()` / `live_transactions()` rather than bare selects, so a soft-deleted parent is rejected for the same reason a stranger's is. Ownership and visibility stay one rule; see ADR 0007. `NotOwned` subclasses the existing `AppError` with a 404, never a 403 — a budget belonging to someone else must be indistinguishable from one that never existed, which is the same stance the `OwnedBudget` path dependency already took.

`ownership.require_owned` is the single lookup behind both halves: the path-parameter dependencies in `app/dependencies.py` and the payload walk above. Those dependencies were three copies of the same function and are now one `owned_path(model, param_name)` factory over the same registry, so "owned" cannot come to mean one thing for an id in the path and another for an id in the body.

## Considered Options

- **Composite foreign keys in Postgres** (deferred, not rejected): reference `(id, user_id)` on the parent instead of `id`, making a mismatched owner structurally impossible for every write path, present and future. Verified working, including that account deletion still cascades. Deferred for three reasons. It requires mirroring the constraints in `__table_args__`, because `alembic check` against a DB-only version emits `remove_constraint` / `remove_fk` operations — the next autogenerate would delete the security constraint. It blocks ownership transfer: `UPDATE budgets SET user_id = ...` is refused without `ON UPDATE CASCADE`, and with it a one-row update rewrites every descendant. And it encodes "one owner per tree" into the schema, which forecloses shared or household budgets — an open product question. The application check is a prerequisite for good errors either way, since a constraint violation arrives as an `IntegrityError` on a poisoned transaction rather than a 404.
- **Explicit `require_owned(...)` calls in each service** (rejected): plainer, greppable, no annotation walking, and closer to the rest of this codebase's style. Rejected because it leaves the omission failure silent — a new foreign key on a schema is checked only if someone remembers, which is the exact thing that produced the bug.
- **A router dependency that reads the request body** (rejected): FastAPI supports it, and it would keep the check in the HTTP layer where the 404 lives. But `create_bulk_expenses` takes a list, and `convert_json_to_budget` creates Expense rows without passing through a single-row route at all, so a router-level guard would cover the easy paths and miss the ones most likely to be wrong.
- **Checking nothing and relying on `user_id` scoping at read time** (rejected): the planted row is visible through its *parent*, and the parent's owner is the victim. Read-side scoping by `user_id` cannot see the problem.

## Consequences

**Every referenced foreign key costs one query per create.** `create_bulk_expenses` verifies per row, so importing 100 expenses issues 200 lookups before the insert. That is deliberate — a bulk endpoint that skipped the check would be the obvious way around it — but it is the first thing to batch if bulk import ever gets large.

**Mocked router tests must now stub the ownership lookup.** `mock_db.scalars` returns `None` by default, so a create test that does not stub it gets a 404 instead of a 201. The stub can only say "a row came back", never "the row is yours", which is why the real assertions live in `tests/test_ownership.py` against Postgres. A mocked session cannot test this mechanism at all.

**`CategoryCreate.user_id` is the one exemption**, recorded in `EXEMPT_FIELDS` with its reason: it is the owner column rather than a reference to a parent, and it is still client-supplied because the categories router has no `CurrentUser` to take the owner from. That is a real remaining hole, not a design position — it closes when categories get authenticated, and the exemption should be deleted then.

**The check ties a row to the *session*, not merely to a consistent owner.** A future path that accepts `user_id` from input — an admin tool, an importer — can satisfy every check here while still being wrong. The composite-FK option would not fix that either; only taking the owner from the session does.

**`OwnedRef[Budget]` does not work and must not be reintroduced.** A PEP 695 generic alias does not substitute its type parameter inside `Annotated` metadata, so `Owned(T)` stays a TypeVar, the marker silently disappears, and the field ends up unchecked while looking checked. Hence four concrete aliases — `BudgetRef`, `CategoryRef`, `ExpenseRef`, `TransactionRef` — and a comment saying why.

**The markers are invisible to clients.** `Owned` is `Annotated` metadata, so the OpenAPI schema still shows a plain UUID. Nothing about the ownership requirement is discoverable from the generated docs; a client learns about it by receiving a 404.

**`owned_path` builds its signature with `inspect.Signature`.** FastAPI matches a dependency's parameter *name* against `{budget_id}` in the route path, so the factory cannot use a shared `obj_id` parameter without renaming every route's path variable and its OpenAPI entry.
