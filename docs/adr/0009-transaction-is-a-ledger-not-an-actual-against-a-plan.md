# Transaction is a ledger of money movement, not an actual measured against a plan

**Status**: accepted (supersedes ADR-0002; `saving_goal` reasoning amended by ADR-0011)

ADR 0002 modeled income as a recurring Expense so that rollover, planned-vs-actual and
budget totals would work uniformly across income and spending. In practice income has no
plan to be measured against - pay varies month to month, and a user observes income rather
than committing to it - so an income Expense carried `cost` equal to its own actual,
`frequency` permanently `once`, a meaningless generated `monthly_cost`, a `series_id` with
no lineage, and a `category_id` that existed only to satisfy a NOT NULL constraint.

We redefined **Transaction** as any change to the money available to a User - money in,
money out, and transfers between Expenses - and made `transactions.expense_id` nullable.
Income is a Transaction with no Expense. There is no Income table, no `is_income` flag on
Expense, and no reserved income Category.

## Considered Options

- **Keep income as a recurring Expense** (ADR 0002, rejected): every mechanism that made
  income fit had to be invented for income alone - a forced single income Category, an
  auto-create-on-transaction path, a "does not roll" marker - each one a workaround for a
  case that was not a member of the entity.
- **`Expense.is_income` flag** (rejected): moves the classification onto the right row, but
  leaves the seven dead columns in place and still requires a Category to satisfy the FK.
- **A separate `income` table** (rejected): preserves the invariant that every Transaction
  references an Expense, at the cost of a second money-movement table whose rows differ from
  Transaction only in lacking a plan, plus a UNION for any combined "money in and out" view.
- **Nullable `expense_id` on Transaction** (chosen): one table, one shape to reason about
  for spending, transfers and income alike, matching how the domain actually talks about it.

## Consequences

`expense_id` is nullable, so every join from Transaction to Expense must handle its absence,
and the invariant "a Transaction is an actual measured against a plan" no longer holds -
that property now belongs only to the subset with a non-null `expense_id`.

Income is user-scoped and observed-only. A Budget cannot answer "can I afford this?" ahead
of the money arriving, because there is no planned inflow figure anywhere. That is the
accepted cost of this reversal. The intended remedy, if it is ever needed, is a single
nullable `expected_monthly_income` on Budget - additive, and deliberately out of scope here.

`Transaction.name` is required, because an income row has no Expense to take its identity
from. Expense-linked rows carry a name too rather than deriving one.

`Category.system_type` is dropped entirely along with the `category_system_type` enum type.
Its three members are all gone: `income` because income is no longer an Expense, `saving_goal`
because a savings goal is carried without the Category knowing (at the time by
`Expense.goal_amount` / `goal_date` / `amount_saved`; `amount_saved` has since moved to a
separate Savings entity per ADR-0011, which does not change the conclusion here), and `debt`
because a carried-forward Expense is an ordinary Expense (see ADR-0010). A Category is now a user-owned label with no business-logic role.
