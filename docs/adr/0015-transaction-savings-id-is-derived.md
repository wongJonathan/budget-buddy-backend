# A Transaction's fund is derived from its Expense, not recorded on it

**Status**: accepted (supersedes ADR-0011's "Which fund a Transaction moved is recorded, not derived")

ADR-0011 stored `transactions.savings_id` so that a fund's history could never move: the fund
money *went into* was treated as a fact about the past, distinct from the fund a lineage *feeds
now*. The case that needed the distinction was Activation's reallocation map re-pointing
`expenses.savings_id`. Neither Activation nor the map exists, and nothing else re-points a
lineage, so the two columns always gave the same answer.

Meanwhile the stored copy had to be written correctly by every path that touches a Transaction,
and two did not. `update_transaction` moved a SAVE to another Expense and left it counting
toward the old fund, and the JSON import wrote its SAVE with no fund at all, so imported savings
read zero. A User only ever assigns a Transaction to an Expense, never to a fund, so the fund
should follow the Expense the same way the Category does.

We decided that **`Transaction.savings_id` is a read-only `column_property` derived through
`expense_id`**, and the column is dropped.

## The rule

A Transaction's fund is its Expense's `savings_id` when its type is `SAVE`, `SPEND_SAVED` or
`TRANSFER`, and null otherwise.

- The type gate is what keeps it from being a copy of the Expense's column. The `SPEND` half of a
  split has the same `expense_id` as its `SPEND_SAVED` half, but it drew on the Pool.
- The Pool side of a TRANSFER pair has no Expense, so the subquery returns null on its own.
- The derivation does not look at the Expense's `deleted_at`. A balance counts money, and
  deleting the plan does not unsave it. ADR-0014's withdrawal still works through each
  Transaction's own flag.

## Consequences

- Moving a Transaction to another Expense moves it to that Expense's fund. A SAVE moved onto an
  Expense with no fund opens one, as create does.
- Re-pointing `expenses.savings_id` moves every Transaction of that row with it. This is the
  trade ADR-0011 declined, and it is accepted until something actually re-points. When
  Activation is designed, it must either carry funds by pointing new rows at the existing fund
  (which derivation handles), or bring a stored column back.
- `balance()` can no longer use an index on `transactions.savings_id`. At this app's scale that
  does not matter (ADR-0011's performance note still applies).
- Still not guarded: moving a SAVE off a fund whose money was already spent through
  `SPEND_SAVED` can take that fund below zero, and editing a split spend does not redo the split.
