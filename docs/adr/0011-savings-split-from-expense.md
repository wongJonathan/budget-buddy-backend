# Savings is a separate entity, and Met measures allocation drawn from the pool

**Status**: accepted (amends ADR-0009 and ADR-0010; its "Deleting" section is extended by ADR-0014, which also withdraws the deleted Expense's Period Transactions and adds Restore)

`Expense` carried both the plan (`cost`, `frequency`, `monthly_cost`) and the money set aside
against that plan (`amount_saved`), and separately `TransactionType` already had `SAVE` and
`SPEND_SAVED` members. Two representations of "saved" existed, neither derived from the other
and nothing reconciling them — `amount_saved` was written on create, update and JSON import,
and read by no code path at all.

The deeper problem is grain. An `Expense` is instantiated **once per Period**
(`uq_expense_budget_period_series`), while a savings balance accumulates **across** Periods.
ADR-0010 gives a carried Expense a fresh `series_id` and no link back, and ADR-0003 has
Activation instantiate fresh rows for a draft Budget, so a balance stored on an Expense row is
either duplicated into every Period and every competing plan, or lost at the Period boundary.
`amount_saved` was on the wrong row.

We split **Savings** out as its own entity: user-scoped, one per Expense lineage, holding no
balance of its own. Its balance is derived from the Transaction ledger. `Expense.amount_saved`
is dropped.

## What Savings is

A `savings` row is `id`, `user_id`, `is_deleted`, and nothing else. That is not a sign the
entity is thin — it is the whole point. `goal_amount` and `goal_date` stay on Expense (below),
and the balance lives in the ledger, so what remains is **durable lineage identity**: the only
thing in the schema that can say "these fourteen Expense rows across three Periods are the same
real fund of money". `series_id` cannot do this, because ADR-0010 deliberately gives every
carried Expense a fresh one.

`expenses.savings_id` is a nullable FK. Many Expense rows point at one Savings — the rows of a
single lineage across Periods — so there is deliberately **no `UNIQUE` constraint** on it; one
would reject next month's row the first time Rollover ran. "One Savings per Expense" is a
statement about concepts, enforced by only ever pointing a lineage's rows at the fund they
inherited.

The row is created **lazily**, by the server, on the first `SAVE` against a lineage. Most
Expenses never save anything, and a null `savings_id` is the honest signal for that: no fund, no
split, no special case. Historical rows keep `NULL`; Rollover propagates the id forward from the
moment it exists.

## The arithmetic

Money **leaves the pool** when it is saved. A fund is a separate bucket, not an earmark on money
still in the pool — which is what makes "spend from savings doesn't touch this month's budget"
mean anything.

```
pool   = income − save − spend + transfers(rows with null expense_id)
fund(S) = save − spend_saved − transfers(rows on S's lineage)
```

Each transaction type moves exactly one pair of these:

| type          | fund | pool | counts toward Met |
| ------------- | --- | ---- | ----------------- |
| `INCOME`      | —   | +A   | no                |
| `SPEND`       | —   | −A   | **yes**           |
| `SAVE`        | +A  | −A   | **yes**           |
| `SPEND_SAVED` | −A  | —    | no                |
| `TRANSFER`    | −A  | +A   | no                |

### Which fund a Transaction moved is recorded, not derived

`transactions.savings_id` and `expenses.savings_id` both exist and are **not duplicates**:

- `transactions.savings_id` — which fund this money *went into*. Historical, never rewritten.
- `expenses.savings_id` — which fund this lineage *currently feeds*. Re-pointable later.

They give the same answer today, which is why deriving the first from the second by joining
`transactions.expense_id → expenses.savings_id` looked like the cheaper option and was the
original design. It breaks on the reallocation map above: re-pointing a lineage would move every
historical `SAVE` to the new fund and empty the old one, when the money demonstrably went into the
old one. A fund's contents are a fact about the past and cannot be read off a pointer that
describes the present.

It also made a balance depend on Expense and Budget visibility. Soft-deleting either put the
join's rows outside `live_transactions`, and the fund read zero while the money was still real.

So `balance()` sums by `Transaction.savings_id` and filters only on the Transaction's own
`is_deleted` — **the one read in the codebase that deliberately does not build on
`live_transactions`**. The ancestor rule is right for listing what a user can see and wrong for
counting what a user has: a hidden row is a display decision, a vanished balance is a silent
revaluation. A fund ends through its own `is_deleted`; a movement stops counting through the
Transaction's.

`savings_id` is non-null on exactly the rows that move a fund — `SAVE`, `SPEND_SAVED`, and the fund
side of a `TRANSFER` pair — and null on `SPEND`, `INCOME` and a transfer's Pool side. The cost of
recording rather than deriving is that the write path must stamp it, and a bug there orphans a
movement from its fund. That is a loud, testable failure, which the alternative's silent
revaluation is not.

Balances are **derived, never stored**. Performance is not the reason to worry: a decade of
heavy use is ~36k rows for a whole account, and a `SUM` filtered by an indexed lineage touches a
few hundred. The real trade is that "a fund never goes negative" spans rows and so cannot be a
`CHECK` — it needs an application-level guard inside the write transaction. That is preferable
to a stored balance, which is the drift this ADR exists to remove and which every write path
(create, update, soft-delete, un-delete, bulk import, Rollover, Activation) would have to
remember to maintain.

## Met is redefined

ADR-0010 defined an Expense as **met** when its Transactions total at least its `monthly_cost`.
That is now:

> **Met**: an Expense whose _pool-drawing_ Transactions for its Period total at least its
> `monthly_cost`.

`monthly_cost` is the allocation an Expense draws from the pool, so `SAVE` and `SPEND` count and
`SPEND_SAVED` and `TRANSFER` do not. One criterion, no netting, no per-Period special case, and
Met's predicate is now the same one the pool arithmetic uses.

Two consequences that look wrong and are not:

- Spend £50 on Groceries with £30 in its fund and the Expense moves by **£20**, because the £30
  was counted when it was saved. Counting it again at spend time is the double-count.
- Spend June's car repair purely from its fund and June's Expense stays **unmet**, so Rollover
  carries a `once` row. Correct: January's allocation was consumed, not June's.

## Spending is split by the server

A spend against an Expense with a funded fund draws from the fund first and the remainder from the
pool, written as a `SPEND_SAVED` row plus a `SPEND` row. The client cannot declare which pool it
drew from — it is unverifiable and can overdraw a fund — so `SPEND_SAVED` is rejected on **both**
create and update. (`TransactionUpdate.type` is what makes the update half necessary: without it
a client `PATCH`es an ordinary `SPEND` into a `SPEND_SAVED` and walks around the create-time
guard.) The rule is unconditional, with no opt-out flag, which keeps the split a pure function of
`(expense, amount)` and therefore reproducible after an edit.

The two rows are **not linked**. Once written they are separate transactions describing two
genuinely distinct money movements, and both balance formulas sum row-by-row, so nothing breaks
if they are edited independently. The "£50 purchase" is a UI concept, not a ledger one — the same
reasoning that makes Income a bare Transaction. Accepted cost: deleting the `SPEND` half meaning
to undo the purchase leaves the `SPEND_SAVED` half in place, and £30 stays gone from the fund.

## Transfer stays, and is Met-neutral

`TRANSFER` moves money **fund to pool** and nothing else: one row carrying the `expense_id` and
`savings_id` of the source, and one with both null for the Pool side, paired by `transfer_id`.
Direction is readable off those columns, so `transfer_id` is purely the pairing link. The server writes both
rows in one request — a half-failed pair is money that exists on one side of a transfer and not
the other, which no read path could detect.

It cannot be composed from the other types. `SPEND_SAVED` + `SAVE` looks like a fund-to-fund
transfer and **destroys money**: `SPEND_SAVED` takes £100 out of fund A without crediting the
pool, then `SAVE` credits fund B while debiting the pool a second time, for a net pool loss of
£100 on a move that never left the user's control. More generally, nothing in the table above
produces `(fund −A, pool +A)` except `TRANSFER`; only `INCOME` credits the pool, and it asserts
money arrived from outside.

A signed `SAVE` (negative amount) would produce the same balance movements with no new type, and
was rejected: it would carry Met `−A` by the ordinary rule, and exempting it would be a special
case. `TRANSFER` being Met-neutral is the substantive difference between the two, so they are not
the same event with a sign flip.

Fund-to-fund is therefore `TRANSFER` (A to pool) followed by `SAVE` (into B) — money conserved, A's
Met untouched, and B's Met satisfied by the ordinary rule because at that instant the money
genuinely is in the pool being allocated. A pool-to-fund `TRANSFER` is deliberately not
representable: it would be indistinguishable from `SAVE` except in its Met effect, which is
exactly the near-duplicate that let `amount_saved` and `SAVE` drift apart.

Known cost: save £100 into A in June and transfer it back out in June, and A reads **Met** with
an empty fund. The ledger shows the truth, and the case is a same-period correction pattern, but
for a savings goal it records a month's contribution as done while the money is not there.

## The goal stays on Expense

`goal_amount` and `goal_date` do not move to Savings. `goal_date` **cannot** move: it is
referenced inside `_MONTHLY_COST_EXPR`, and a `GENERATED ALWAYS AS` column can only reference
columns on its own row. `goal_amount` could have moved and does not, because storing either on
both rows would be two writable copies of one fact — the drift this ADR exists to remove. The
savings view's need for them is a _read_ need, answered by the link, not a column.

They read like a pair and are not: `goal_date` is a pacing input to the plan (how fast must this
be funded), `goal_amount` is the fund's target.

## Deleting

Deleting the **current-Period** Expense row soft-deletes its Savings and writes a `TRANSFER` pair
draining the fund back to the pool. Deleting a historical row does not: past rows are a record,
and only the current-Period row represents a live intention. Older rows keep a `savings_id`
pointing at a soft-deleted fund, which is fine because visibility is derived at read time
(ADR-0007) rather than propagated.

Draining to the pool is a `TRANSFER`, **not** an `INCOME`. The money never left the user's
control, so an income row would assert an arrival that did not happen and inflate the account
above the bank.

The active Budget cannot be deleted at all. Combined with Activation's constraint below, this
gives the invariant **only the active Budget has funded funds** — so no Budget that _can_ be
deleted ever holds money, and the "what happens to fifteen funded funds on one click" question
does not arise.

## Constraint on Activation, which is not yet implemented

**Activation must be refused while any fund under the currently-active Budget holds money.** The
user drains or spends first, then switches. Without this the invariant above breaks the moment
someone activates a draft: ADR-0003 has Activation instantiate fresh rows, so the new lineage
starts empty while the old one keeps the money inside a plan that is no longer live.

This is a placeholder for the intended end state — Activation taking a reallocation map from old
fund to new lineage — and is forward-compatible with it: the refusal is replaced by the mapping
step. Auto-draining every fund on switch was rejected for silently liquidating savings during what
looks like a view change. Name-matching lineages across Budgets was rejected as the implicit
magic ADR-0005 and ADR-0010 both already turned down.

## Two guards this design requires, included in this change

Both are pre-existing gaps that Savings makes dangerous rather than merely wrong:

- **Expenses outside the current Period cannot be edited or deleted.** `CurrentPeriod` guards
  _where a row is written to_, not _which rows can be touched_ — `soft_delete_expense` has no
  Period check, and `update_expense` only validates `period` when the payload supplies one, so a
  historical `cost` can be rewritten today. ADR-0010 already relies on this rule ("a late edit
  cannot change a shortfall Rollover has already acted on"); it just never existed. Savings makes
  it load-bearing, because deleting a 2024 row would otherwise drain a live fund.
- **The active Budget cannot be soft-deleted.** `soft_delete_budget` sets the flag with no check
  against `User.active_budget_id`.

## Migration

`amount_saved` is dropped and its values discarded rather than replayed as synthesized `SAVE`
rows. A derived model can only account for money it has a ledger row for, so discarding it would
normally make balances silently wrong — this is safe only because there are no real users and the
sole source of non-zero values is `convert_json_to_budget`. **The same migration against live
data would need each non-zero `amount_saved` turned into a `SAVE` Transaction dated to its row's
`period`.**

## Not in scope

`_MONTHLY_COST_EXPR`'s `custom` branch divides `cost` by `GREATEST(months_to_goal, 1)`, so past
its `goal_date` a fully-funded goal is re-charged its entire `cost` every month forever, each row
unmet and carrying. Pre-existing and unrelated to this split — but the split makes it visible,
because a funded fund now sits next to an Expense demanding the money again. Fixing it means
altering a generated column and settling what a past-`goal_date` row _should_ cost, so it gets
its own change.
