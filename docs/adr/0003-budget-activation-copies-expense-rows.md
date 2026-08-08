# Budget activation copies Expense rows instead of flipping a status flag

**Status**: accepted

A User can have multiple Budgets (e.g. a draft being planned alongside the live one), and switching which Budget is "active" must not let edits to one leak into the other.

Activation (setting `User.active_budget_id`) instantiates fresh Expense rows for the newly-active Budget rather than toggling a status column on Budget. This is why Budget has no `status`/`is_active` column at all — activeness is purely `User.active_budget_id` pointing at it, and the instantiated Expense rows are what Rollover then walks forward period by period.

## Considered Options

- **Status flag on Budget** (rejected): simpler, but activating Budget B while A is active would need in-place mutation or a swap, and drafts couldn't be edited independently of what's live without extra bookkeeping.
- **Copy/instantiate Expense rows on activation** (chosen): draft and live stay fully independent at the cost of some row duplication.
