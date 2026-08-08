# Rollover walks forward from the latest period, keyed by series_id

**Status**: accepted

Every active Budget needs its Expense rows instantiated for the current Period, whether the user last opened the app yesterday (exact match already exists) or three months ago (multiple periods missing), and whether the Budget was just activated for the first time (no periods exist yet).

Rollover handles all three uniformly with a two-stage check per Budget: if an Expense row already exists for the target Period, use it; otherwise find `MAX(period)` for that `budget_id` and walk forward one month at a time, copying each logical expense into the next Period until reaching the target. Each logical recurring expense carries a stable `series_id` (a UUID set when the expense is first created and copied forward unchanged by Rollover/Activation), which is what lets the walk recognize "this month's Groceries" as the successor of last month's rather than relying on the user-editable `name`.

A unique constraint on `(budget_id, period, series_id)` makes the walk idempotent — if two triggers race to roll the same Budget forward, the second one's insert for an already-created period+series collides instead of duplicating.

## Consequences

Every Expense-creating code path (manual add, Rollover, Activation) must assign or propagate a `series_id`; a one-off Expense with no future recurrence still gets one (of its own), it just never gets copied forward again.
