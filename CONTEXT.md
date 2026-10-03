# Budget Buddy

A personal budgeting backend: users track planned expenses against budgets, organized by category, with actual money movements recorded as transactions.

## Language

**User**:
An individual account holder. Owns Budgets and Categories, and points at the one it currently has open via `active_budget_id`. Created only by Provisioning.

**Provisioning**:
Creating a User account. Always an operator action, never self-service — there is no registration and no route that creates a User. A provisioned account arrives with a blank Budget already active, so a User is never in the state of having no Budget at all. The password set at Provisioning is meant to be temporary, replaced by its owner on first sign-in; nothing enforces that yet.
_Avoid_: "signup", "registration" — nothing here is self-service, and the account holder is not the one who creates the account.

**Budget**:
A named collection of Expenses belonging to a User, rolled forward period by period. A Budget has no stored "active" state of its own — it is active only when `User.active_budget_id` points at it. Soft-deleted via `deleted_at`, never hard-deleted, and never while it is the active one.
_Avoid_: "active budget" as a Budget-side flag/status — activeness is always read from the User side.

**Category**:
A user-owned label applied to Expenses, and nothing more — no reserved subset, no business-logic role, no control over how the Expenses under it behave. Soft-deleted: a deleted Category is absent from lists, unreachable by id, and cannot be applied to a new or edited Expense, but Expenses already carrying it keep it. See `docs/adr/0009`.
_Avoid_: "system category", "income category", "debt category" — these existed as a `system_type` hook and were removed. Sign and lineage are facts about a row, never about its label.

**Expense**:
A line item within a Budget, instantiated once per Period, covering planned spending, savings goals and debt paydown through one shape. It is the *plan* only: money set aside against it belongs to its Savings (`docs/adr/0011`). Income is not an Expense — it has no plan to be measured against (`docs/adr/0009`).
_Avoid_: "budget item" — Expense is the single row type covering all of these. Also avoid treating an Expense as a thing that holds money; it holds an intention, and a Period's worth of it.

**Transaction**:
Any change to the money available to a User: money in, money out, or money moved between the Pool and a Savings. It references the Expense it moved money against, or no Expense at all when it is Income. Always named. One of five types: Spend, Spend Saved, Save, Transfer, Income. Only Save, Spend Saved and Transfer move a Savings, and the one they move is always their Expense's, so reassigning one to another Expense moves it to that Savings. The Spend half of a split shares the Expense but moves no Savings. A Transfer moves money from a Savings back to the Pool and is represented as two rows linked by `transfer_id`. Moving money from one Savings to another is a Transfer to the Pool followed by a Save into the other, so the second Expense's Met moves by the ordinary rule. The rows of one Transfer are a single movement: edited and deleted together, never one at a time. See `docs/adr/0016`. A User may ask for a Transfer, but its rows are always written by the server; Spend Saved is never asked for at all. Its Category is always its Expense's, never its own, and follows that Expense if it is recategorized; Income has none.
A Transaction carries two different whens. Its *date* is when the money moved, as the User asserts it — a day, never in the future, and correctable within the current Period, since a row entered today can record last week's purchase as long as it was this month. A Transaction recorded in an earlier Period is a record like that Period's Expenses: it can't be edited or deleted. See `docs/adr/0017`. When it was *recorded* is a separate fact, set by the server and never editable, and is what orders two movements the User dated the same day. See `docs/adr/0013`.
_Avoid_: "description" — merged into `note`, there is no separate description field. Also avoid reading Transaction as "an actual measured against a plan" — that holds only for the subset that references an Expense. Also avoid treating either when as a stand-in for the other: a corrected date does not change when the row was recorded, and the order rows were recorded in is not evidence about when the money moved.

**Income**:
Money arriving, recorded as a Transaction with no Expense. Always observed after the fact, never planned — nothing anywhere holds an expected inflow figure. Belongs to the User rather than to a Budget: two Budgets are two competing plans against the same real money.
_Avoid_: "income category", "income expense" — income was once modeled as a recurring Expense and is not (`docs/adr/0009`).

**Savings**:
Money a User has set aside against one Expense lineage, and the durable identity of that lineage across Periods. A Carried Expense from a funded lineage draws on the same Savings, so in one Period a Savings can have more than one Expense on it. Holds no stored balance — its balance is the sum of the Transactions stamped with it — and no goal of its own; the goal lives on the Expense. A balance counts undeleted Transactions and nothing else, so hiding an Expense or Budget never changes it. Created the first time money is Saved against a lineage, closed when the last undeleted current-Period Expense on it is deleted (which returns what it holds to the Pool; deleting one of several withdraws only that Expense's own Transactions), and reopened if that Expense is Restored. A User never closes a Savings directly — ending the lineage's Expense is the only way to end its Savings. Its one User-editable field is its note. See `docs/adr/0011`.
_Avoid_: "amount saved" as a figure living on an Expense — that column existed and is gone. Also avoid "SavingFund" and "envelope" as names for the entity; the entity is a Savings and what it holds is its Fund.

**Fund**:
What a Savings holds — the money currently in it, being the sum of the Transactions stamped with it. "Savings" names the entity, "Fund" names its contents; a sentence about a balance wants the second. A Fund can be read as of the end of any Period, as what it held before that Period began plus the net movement within it. A movement belongs to the Period its Transaction is *dated* in, not the Period of the Expense it was made against, because the date is when the money moved.
_Avoid_: "pot", "envelope", "sinking fund" — one word for one concept, and "pot" was the informal name this went by before it had one.

**Pool**:
The money a User has available to allocate right now: Income received, less what has been Saved or Spent, plus what has been Transferred back out of a Savings. Saving moves money out of the Pool and into a Savings, so spending from a Savings later costs the current Period nothing.
_Avoid_: "balance", "budget" — the Pool is the User's spendable money, not a plan and not a Budget's total.

**Soft delete**:
Marking a Budget, Expense, Transaction, Savings or Category as deleted (`deleted_at`, the moment of deletion) without removing the row. A row is visible only if it *and every ancestor* is undeleted, so deleting a Budget hides its Expenses without touching them. Transaction is outside this rule — whether it counts depends only on its own flag, never on an ancestor's, so hiding a Budget or an Expense never silently unspends anything. When an Expense deletion withdraws Transactions, it does so by deleting them explicitly (below). A deleted Transaction, Expense or Savings still shows, flagged as deleted — reachable by id, and in lists when the reader asks for deleted rows — but it is read-only, counts toward no total, and can never be the parent of a new row. A deleted Budget or Category is gone: absent from lists and unreachable by id alike. Category sits outside the ancestor rule in both directions — deleting one hides no Expense. User is the exception — it hard-deletes.
Deleting an Expense withdraws it and its Period's money movements together: either the plan was a mistake, or its money belongs to a different plan. Its Transactions in that Period stop counting, including its side of any Transfer. A Transfer out of it is withdrawn whole, Pool side included, but a Save it gave another Expense's Savings stays, because that money was moved there. A Transfer into it is undone whole, and the money goes back to the Savings it came from. Its lineage ends (nothing succeeds or carries forward from it), and whatever its Savings still holds is Transferred back to the Pool before the Savings is closed. A deleted Expense can be Restored within the same Period.
_Avoid_: "deactivated" — Expense used to call its flag `is_deactivated`, which reads like a state a user chose rather than a deletion. One word for one concept.

**Restore**:
Reversing the deletion of an Expense, possible only within the Period it was deleted in. It brings back exactly what that deletion withdrew — the Expense, its Period's Transactions that the deletion took, and its Savings — and withdraws the Transfer the deletion wrote. Transactions the User had deleted beforehand stay deleted. A Restore may leave the Pool negative, when the returned money has since been allocated elsewhere; that is an accurate picture, not an error. See `docs/adr/0014`.
_Avoid_: "undo", "undelete" — Restore is something done to a row, and one word for one concept.

**Period**:
The real DATE marking which month an Expense instance belongs to, always the first day of that month. Drives Rollover; not a display string like "MM/YYYY". Expenses and Transactions can only be created, edited or deleted in the current Period — neither ahead nor behind. Earlier Periods are a record, not a working area.

**Monthly Cost**:
What one Period of an Expense costs, derived from its `cost` and `frequency` and always expressed per month — `cost` is the figure the user typed, Monthly Cost is what a Period is measured against. Daily and weekly plans convert using the actual number of days in the Period's own month, so the same unchanged plan costs less in February than in March. See `docs/adr/0012`.
_Avoid_: reading it as a fixed property of the plan — it is a property of the plan *in a given Period*, which is why month-over-month comparisons have to normalise per day before they mean anything.

**Met**:
An Expense whose *Pool-drawing* Transactions for its Period total at least its `monthly_cost` — Save and Spend count, Spend Saved and Transfer do not, because that money was counted when it was saved. Measured against `monthly_cost` for every frequency: `cost` is the figure the user typed, `monthly_cost` is what a Period is measured against.
_Avoid_: reading Met as "money was spent on it" — it means a Period's allocation was drawn from the Pool, whether that was spent or set aside.

**Allocated**:
How much of an Expense's Monthly Cost its Period has drawn from the Pool so far — the total of its undeleted Save and Spend Transactions, the same figure Met compares against Monthly Cost. Spending from a Savings adds nothing to it, because that money was Allocated when it was Saved: a £50 purchase covered £30 by Savings raises it by £20. Derived, never stored.
_Avoid_: "spent" — it includes money Saved, not only money Spent, and a £50 purchase does not raise it by £50.

**Rollover**:
The scheduled job that instantiates the next Period's Expense rows from the prior one and carries unmet Expenses forward, walking month-by-month so multi-month gaps are handled like any other step. It rolls only the User's active Budget. Each recurring Expense gets a successor: the same plan in the next Period, in the same lineage. A `once` Expense never gets a successor; if it ends its Period unmet, only its Shortfall moves on, as a Carried Expense. A deleted Expense ends its lineage, so Rollover neither succeeds nor carries it, and the same goes for an Expense whose Category has been deleted (what then happens to a funded one's Savings is not yet decided). A `custom` Expense gets no successor once its Period has passed its goal date, though its last Shortfall still carries. A month nobody could record against still has its Expenses measured, so after a gap, every skipped month's Shortfall is carried, each as its own row. Each step starts from the last Period Rollover processed, never from whatever the newest Expense happens to be. Idempotent by recording which Periods it has processed, never by inspecting whether the target Period looks empty. See `docs/adr/0010`.
_Avoid_: "debt expense" as a distinct kind of row — there is no such kind.

**Shortfall**:
How far an unmet Expense fell short in its Period: its Monthly Cost less its Allocated. An Expense that is exactly Met or over-Allocated has no Shortfall, and overpayment is never credited to the next Period.

**Carried Expense**:
The Expense Rollover creates in the next Period to cover a Shortfall: `frequency = once`, costing the Shortfall, in the source's Category and drawing on the source's Savings, but starting a lineage of its own. Its name and note tell the User where it came from. Nothing else does, and nothing reads them: once created, it is an ordinary Expense that the User can edit, pay, delete or let carry again. Carrying one again keeps its name rather than marking it a second time.
_Avoid_: "debt expense" — it describes where the row came from, not a kind of row.

**Activation**:
Making a Budget the User's current one (`User.active_budget_id`). Activation instantiates fresh Expense rows for the Budget rather than flipping a status flag, so a draft Budget and a live Budget stay fully independent copies. Provisioning is the one other thing that sets `active_budget_id`: the Budget it seeds is blank, so there are no Expense rows for Activation to instantiate. Everything after that goes through Activation.

**Ownership**:
The rule that a row and every parent it points at belong to the same User. Stamping `user_id` on a new row does not establish it - the `budget_id` or `expense_id` in the payload came from the client, so the parent is resolved through an owner-scoped lookup before the row is written. Declared on the schema field (`budget_id: BudgetRef`) rather than written out per service, so adding a foreign key is what causes it to be checked. A parent belonging to someone else is a 404, identical to one that does not exist. See `docs/adr/0008`.
_Avoid_: "permissions", "access control" - there are no roles or grants here, only "is this row yours".
