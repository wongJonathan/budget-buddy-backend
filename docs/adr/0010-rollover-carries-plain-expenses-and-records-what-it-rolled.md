# Rollover carries plain Expenses, and records what it rolled rather than inferring it

**Status**: accepted (definition of *met* amended by ADR-0011)

Rollover runs as a scheduled job. An Expense is **met** when its Transactions total at least
its `monthly_cost` - for every frequency, not just `monthly`, so that `yearly` and `custom`
rows are measured against the same normalized per-period figure the rest of the app uses.
An unmet Expense is carried into the next Period regardless of its frequency; a met Expense
with `frequency = once` is not.

> **Amended by ADR-0011**: only *pool-drawing* Transactions count - `SAVE` and `SPEND`, not
> `SPEND_SAVED` or `TRANSFER`. Money spent from a savings fund was already counted toward Met
> when it was saved, so counting it again at spend time double-counts a single allocation.

A carried Expense is an ordinary Expense with `frequency = once` and **no marker of any kind**
- no flag, no reserved Category, no link back to the shortfall it came from. It rolls, gets
paid, soft-deletes and reports exactly like any other row, because it is one. Its
`frequency = once` is what stops it carrying forever: once met, it does not roll again.

Idempotency comes from a record of which `(budget_id, period)` pairs Rollover has already
processed, not from checking whether the target Period looks empty.

## Considered Options

- **`carried_from_expense_id` self-FK** (rejected): would have made a re-run recognise its own
  prior output, and would have answered "what do I owe, and on what". Rejected because a
  carried Expense genuinely behaves like every other Expense, and the column would be the only
  thing suggesting otherwise.
- **Deterministic `series_id`** (rejected): deriving the carry's `series_id` from its source
  (rather than `gen_random_uuid()`) would let the existing `(budget_id, period, series_id)`
  constraint reject a duplicate, with no new column - but the link would live inside a hash,
  invisible in the schema and undebuggable from a constraint violation.
- **Fold the shortfall into next Period's `cost`** (rejected): no extra row at all, but it
  overwrites the user's planned figure with a computed one, and a `once` Expense has no
  next-Period row to fold into - so carried rows would be needed for that case anyway.
- **Skip Rollover if the target Period already has Expenses** (rejected): `ExpenseCreate.period`
  is client-set, so a single user-created row in the target Period disables Rollover for that
  whole month. Restricting creation to the current Period narrows this but does not close it -
  a job that fails and retries, or catches up after an outage, still finds user rows and skips.
  The check reads a proxy ("does this Period look untouched?") for the fact it needs
  ("did Rollover run for this Period?").
- **A record of rolled Periods** (chosen): reads the fact directly, so retries and multi-month
  catch-up are both correct.

## Consequences

There is no query for "how much debt do I have, and against what" - a carried Expense is
indistinguishable from one the user created. That was the original motivation for a reserved
debt Category, and it is deliberately given up.

Two constraints exist to keep Periods well-formed enough for this to work, and both are
load-bearing rather than tidiness:

- `Expense.period` is truncated to the first of the month (validator plus a DB CHECK). Without
  it, `convert_json_to_budget`'s `date.today()` and Rollover's first-of-month disagree, and
  every `period =` comparison, plus the `(budget_id, period, series_id)` constraint, silently
  stops matching.
- Expenses may only be created in the current Period, in both directions. Planning ahead is
  what draft Budgets are for (ADR-0003); blocking backdating keeps rolled Periods immutable,
  so a late edit cannot change a shortfall that Rollover has already acted on. Evaluated
  against the server clock in UTC.
