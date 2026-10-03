# Rollover walks forward from the latest period, keyed by series_id

**Status**: accepted (starting point amended below)

> **Amended**: the walk starts from the latest Period recorded in `rolled_periods` for the
> Budget, and only falls back to the latest Expense Period *strictly before* the target when
> the Budget has never been rolled. `MAX(period)` alone reads a proxy: a user who adds an
> Expense in the new month before a late job runs makes `MAX(period)` the target, and the
> previous month is never rolled. This is the same reasoning ADR-0010 applies to idempotency.
> A `custom` Expense gets no successor once its Period has passed `goal_date`. Past it,
> `monthly_cost` divides by `GREATEST(..., 1)` and would bill the whole `cost` every month.
> Its last Shortfall still carries.

Every active Budget needs its Expense rows instantiated for the current Period, whether the user last opened the app yesterday (exact match already exists) or three months ago (multiple periods missing), and whether the Budget was just activated for the first time (no periods exist yet).

Rollover handles all three uniformly with a two-stage check per Budget: if an Expense row already exists for the target Period, use it; otherwise find `MAX(period)` for that `budget_id` and walk forward one month at a time, copying each logical expense into the next Period until reaching the target. Each logical recurring expense carries a stable `series_id` (a UUID set when the expense is first created and copied forward unchanged by Rollover/Activation), which is what lets the walk recognize "this month's Groceries" as the successor of last month's rather than relying on the user-editable `name`.

A unique constraint on `(budget_id, period, series_id)` makes the walk idempotent — if two triggers race to roll the same Budget forward, the second one's insert for an already-created period+series collides instead of duplicating.

## Consequences

Every Expense-creating code path (manual add, Rollover, Activation) must assign or propagate a `series_id`; a one-off Expense with no future recurrence still gets one (of its own), it just never gets copied forward again.
