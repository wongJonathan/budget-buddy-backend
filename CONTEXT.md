# Budget Buddy

A personal budgeting backend: users track planned expenses against budgets, organized by category, with actual money movements recorded as transactions.

## Language

**User**:
An individual account holder. Owns Budgets and Categories, and points at the one it currently has open via `active_budget_id`. Created only by Provisioning.

**Provisioning**:
Creating a User account. Always an operator action, never self-service — there is no registration and no route that creates a User. A provisioned account arrives with a blank Budget already active, so a User is never in the state of having no Budget at all. The password set at Provisioning is meant to be temporary, replaced by its owner on first sign-in; nothing enforces that yet.
_Avoid_: "signup", "registration" — nothing here is self-service, and the account holder is not the one who creates the account.

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

**Soft delete**:
Marking a Budget, Expense or Transaction as deleted (`is_deleted`) without removing the row. A row is visible only if it *and every ancestor* is undeleted, so deleting a Budget hides its Expenses and their Transactions without touching them. Deleted means gone: a soft-deleted row is absent from lists and unreachable by id alike. Category and User are the exceptions — they hard-delete.
_Avoid_: "deactivated" — Expense used to call its flag `is_deactivated`, which reads like a state a user chose rather than a deletion. One word for one concept.

**Period**:
The real DATE marking which month an Expense instance belongs to. Drives Rollover; not a display string like "MM/YYYY".

**Rollover**:
The mechanism that instantiates next-period Expense rows from the prior period: exact match on the target Period reuses existing rows, otherwise it walks forward month-by-month from the latest existing Period for that Budget. Handles first-activation and multi-month gaps the same way.

**Activation**:
Making a Budget the User's current one (`User.active_budget_id`). Activation instantiates fresh Expense rows for the Budget rather than flipping a status flag, so a draft Budget and a live Budget stay fully independent copies. Provisioning is the one other thing that sets `active_budget_id`: the Budget it seeds is blank, so there are no Expense rows for Activation to instantiate. Everything after that goes through Activation.

**Ownership**:
The rule that a row and every parent it points at belong to the same User. Stamping `user_id` on a new row does not establish it - the `budget_id` or `expense_id` in the payload came from the client, so the parent is resolved through an owner-scoped lookup before the row is written. Declared on the schema field (`budget_id: BudgetRef`) rather than written out per service, so adding a foreign key is what causes it to be checked. A parent belonging to someone else is a 404, identical to one that does not exist. See `docs/adr/0008`.
_Avoid_: "permissions", "access control" - there are no roles or grants here, only "is this row yours".
