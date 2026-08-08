# Budget Buddy

A personal budgeting backend: users track planned expenses against budgets, organized by category, with actual money movements recorded as transactions.

## Language

**User**:
An individual account holder. Owns Budgets and Categories, and points at the one it currently has open via `active_budget_id`.

**Budget**:
A named collection of Expenses belonging to a User, rolled forward period by period. A Budget has no stored "active" state of its own — it is active only when `User.active_budget_id` points at it. Soft-deleted via `is_deleted`, never hard-deleted.
_Avoid_: "active budget" as a Budget-side flag/status — activeness is always read from the User side.

**Category**:
A user-owned label applied to Expenses. Most Categories are freely named by the user, but `system_type` marks a reserved subset (`income`, `saving_goal`, `debt`) that business logic (rollover, income/expense linking, budget-total filtering) keys off of instead of the user-editable name.
_Avoid_: treating `system_type` as just another user-facing "type" field — it's a reserved business-logic hook, distinct from arbitrary category naming.

**Expense**:
A line item within a Budget, instantiated once per Period. Covers planned spending, savings goals, debt paydown, and — via a Category with `system_type = income` — recurring income, all through the same shape. There is no separate SavingFund, BudgetItem, or Income table.
_Avoid_: "budget item" — Expense is the single row type covering all of these.

**Transaction**:
An actual money movement recorded against a specific Expense. Always references an Expense (including Income transactions — income is not auto-generated, it's logged against the recurring "Income" Expense like any other transaction). One of five types: Spend, Spend Saved, Save, Transfer, Income. A Transfer is represented as two Transaction rows linked by `transfer_id`.
_Avoid_: "description" — merged into `note`, there is no separate description field.

**Period**:
The real DATE marking which month an Expense instance belongs to. Drives Rollover; not a display string like "MM/YYYY".

**Rollover**:
The mechanism that instantiates next-period Expense rows from the prior period: exact match on the target Period reuses existing rows, otherwise it walks forward month-by-month from the latest existing Period for that Budget. Handles first-activation and multi-month gaps the same way.

**Activation**:
Making a Budget the User's current one (`User.active_budget_id`). Activation instantiates fresh Expense rows for the Budget rather than flipping a status flag, so a draft Budget and a live Budget stay fully independent copies.
