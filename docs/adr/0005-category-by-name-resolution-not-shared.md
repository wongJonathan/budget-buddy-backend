# Category-by-name resolution logic is duplicated, not shared, between JSON import and quick-create

**Status**: accepted

Both `budget_service.convert_json_to_budget` and the expense quick-create path (extending `ExpenseCreate`) resolve a `Category` from a user-supplied name. They intentionally use different policies — JSON import matches an existing `(user_id, name)` Category and reuses it silently, while quick-create always inserts and errors (`409`) on a name collision — because the two flows have different failure semantics: import is closer to an idempotent re-sync (re-uploading the same file shouldn't spawn duplicate categories), while quick-create's frontend has already checked for an existing match before sending a bare name, so a collision reaching the backend signals a stale client or a race that should surface loudly rather than merge silently into a possibly-wrong category.

We did not extract a shared "resolve-or-create category" helper for the two call sites. `convert_json_to_budget` must stay a single flush-then-commit transaction across the whole budget/category/expense import (so a mid-import failure rolls back everything); a shared helper following the rest of this codebase's service convention (e.g. `create_category`, which calls `db.commit()` itself) would introduce a commit boundary partway through that transaction and break the rollback guarantee.

## Considered Options

- **Shared "resolve-or-create category" service function** (rejected): removes duplicate logic, but either forces `convert_json_to_budget` to give up its single-transaction guarantee, or forces the shared helper to defer committing (via `flush`, not `commit`) in a way that's inconsistent with how every other service function in `app/services/` behaves.
- **Duplicate, policy-divergent implementations** (chosen): a few extra lines of near-identical matching logic, in exchange for each call site owning its own transaction boundary and failure policy.

## Consequences

A future maintainer who notices the duplicated category-matching code between `budget_service.py` and the expense quick-create path may be tempted to unify it — this ADR is the reason not to, unless the transaction-boundary and duplicate-handling-policy differences are resolved first.
