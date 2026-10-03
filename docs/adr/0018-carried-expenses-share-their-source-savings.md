# Carried Expenses share their source's Savings, and a shared Savings closes with its last Expense

**Status**: accepted (amends ADR-0011's "one Savings per lineage" and ADR-0014's delete rule)

When a funded Expense ("Holiday, £100/month", £60 Saved) ends its Period unmet, Rollover carries its £40 Shortfall as a `once` Expense with a new `series_id` (ADR-0010). If that Carried Expense started with no Savings, catching up would open a second fund. Once the carry was Met it would never recur, and nothing could ever delete it to close that fund, so the money would be stranded. The Carried Expense therefore copies its source's `savings_id`: catch-up money goes into the Holiday fund, and spending from either row draws on it.

That means one Savings can now have more than one undeleted Expense on it in the same Period. ADR-0014's delete rule (deleting an Expense drains its Savings to the Pool and closes it) would let a user who deleted "DEBT Holiday" to give up the catch-up empty the fund under the still-live "Holiday". So the drain and close happen only when the deleted Expense is the **last** undeleted current-Period Expense on that Savings. Otherwise, deleting it withdraws only its own Transactions, and the fund stays open. Restore mirrors this: it reopens the Savings only if the deletion closed it.

## Considered Options

- **Carry with no Savings** (rejected): strands money in a fund nothing can close.
- **Funded Expenses carry no Shortfall** (rejected): breaks "an unmet Expense carries regardless of frequency" for one class of Expense, and undersaving would silently vanish.
- **Deleting either row closes the fund** (rejected): deleting the catch-up would empty the goal.

## Consequences

`expenses.savings_id` already had no UNIQUE constraint (ADR-0011), so this needs no schema change. But the delete path in `app/services/transaction.py` and Restore must check for other Expenses on the Savings before draining it, and that has to land before Rollover ships, since Rollover is what first creates the shared state.
