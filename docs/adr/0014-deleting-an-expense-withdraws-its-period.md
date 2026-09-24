# Deleting an Expense withdraws its Period's Transactions, and Restore reverses it exactly

**Status**: accepted (supersedes in part ADR-0007 and ADR-0011)

ADR-0007 as amended made deleting an Expense hide the plan and leave its Transactions counting,
because a Transaction records real money. That answers the wrong question for how the delete
button is actually used. A User deletes an Expense for one of two reasons: the plan was a mistake,
or its money belongs to a different plan. In both cases they want the Period's movements gone
along with it, and with them still counting, the Expense's Spends and Saves sit in the ledger
against nothing and the User has to delete each one by hand.

We decided that **deleting an Expense withdraws it and its Period's money movements together, and
Restore reverses exactly that.**

## Delete

In one database transaction:

1. Every undeleted Transaction whose `expense_id` is the deleted row is soft-deleted. That means
   every Transaction on that row, including the `SPEND_SAVED` half of a split spend, which gives
   its money back to the Savings before step 2 reads the balance. It is keyed on `expense_id`, not
   on `date`, because a Transaction's date is correctable and independent of its Expense's Period.
2. Whatever the Savings still holds (earlier Periods' Saves, less what they spent) is returned to
   the Pool as a `TRANSFER` pair, and the Savings is closed. This is the ADR-0011 drain, unchanged.
3. The Expense is soft-deleted. Its lineage ends: Rollover neither succeeds nor carries it.

Deletion stays restricted to the current Period (`_require_open_period`).

A cost the User accepts: if they have already spent against the Expense this Period and delete
it, the Pool reads higher than the bank by that amount until they re-record the spend against
another Expense. Stopping a plan without erasing its Period is done by deleting next Period's row
after Rollover, before anything is recorded against it. That is expected to be the common case.
No separate End action is built until users actually run into this.

## Restore

Only within the Period the Expense was deleted in. By the next Period, Rollover has processed the
deleted one, and `rolled_periods` means it never will again. Restore:

1. un-deletes the Expense and the Savings,
2. un-deletes the Transactions **whose `deleted_at` equals the Expense's**,
3. soft-deletes the `TRANSFER` pair the deletion wrote.

The `deleted_at` match is what tells "deleted by this Expense's deletion" apart from "deleted by
the User beforehand", which ADR-0007 identified as the reason write-time propagation makes restore
ambiguous. It works because of two rules, and relies on both:

- **The whole delete is one database transaction**, so Postgres `now()` gives every row the same
  timestamp. Splitting it into several commits breaks Restore without any visible error.
- **A Transaction withdrawn with its Expense is read-only** (409) until the Expense is Restored. No
  edit, delete or individual restore can move its `deleted_at` off the match.

This depends on `deleted_at` replacing `is_deleted`, which lands separately and first.

A Restore may leave the Pool negative, when the money the deletion returned has since been
allocated elsewhere. That is allowed. Refusing would strand the User, a negative Pool accurately
describes over-allocation, and nothing else guards the Pool either.

## Deleted rows are shown, not gone

For Transaction and Expense, lists and lookup by id **include deleted rows by default**, flagged,
and `include_deleted=false` hides them. A deleted row is read-only, counts toward no total, and is
never a valid parent: creating a row that references it is refused. The owner gets 409 for these
("restore it first"), while other Users still get 404 (ADR-0008), because ownership is checked
first. Budget and Savings keep "deleted means gone". Budget deletion is out of scope here.

## Considered Options

- **Leave Transactions counting on delete** (ADR-0007 as amended). Rejected because it doesn't
  match what the delete button is for, as above. It remains the right model for *hiding* through
  an ancestor: whether a Transaction counts still depends only on its own flag, and hiding a
  Budget never unspends anything.
- **Return the Savings' remainder as `INCOME`.** For the Pool it is the same as `TRANSFER`, so it
  prevents no drift. It is worse in three ways: an unstamped row leaves the closed Savings still
  reading its old balance, it overstates Income, and an Income row has no `expense_id` for Restore
  to find it by.
- **An explicit `deleted_with` link instead of matching `deleted_at`.** Its meaning is clearer, but
  it costs a column. The match was chosen, and the two rules above are what it depends on.

## Consequences

- `visibility.live_transactions` stops being "what counts". Money sums must filter the
  Transaction's own flag explicitly now that lists include deleted rows by default. Keep
  "what a reader sees" and "what counts" as separate helpers so they cannot drift.
- `ownership.require_owned` must keep resolving parents through a live-only lookup, even though
  reads no longer are.
- Money already recorded in an Expense that is then deleted is the User's to redistribute.
