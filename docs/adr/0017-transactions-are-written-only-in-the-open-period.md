# Transactions are written only in the open Period

**Status**: accepted (narrows ADR-0013: a date stays correctable, but only within the current Period)

Expenses could already be written only in the current Period ("Earlier Periods are a record"), but Transactions could not. A Save could be made in October against September's Expense, a row could be dated next month, and a PATCH could move an October row into August. Each of these rewrites a closed Period's figures after the fact: its Allocated, and, now that a Fund can be read as of the end of any Period, its fund figures too.

## The rule

- **Create**: `date` must be in the current Period and not after today, and the Expense, if there is one, must be in the current Period. This applies to every type, Income included, and to every item of a bulk create. Before this, only Transfers were held to it (`_endpoint`).
- **Edit and delete**: a row recorded in an earlier Period is read-only (409). "Recorded" means `created_at`, truncated to its month in UTC to match `current_period()`. A PATCH that changes `date` or `expense_id` is held to the create rule.
- **Server-written rows** (the halves of a split, Transfer groups, and `close_savings`'s drain) are dated today and so satisfy the rule by construction.

`created_at` gates edits rather than `date` because the question is about when the row was recorded, which is the one thing `created_at` reliably answers (ADR-0013). For any row written under this rule, the two fall in the same Period.

## Consequences

- ADR-0013's reason for `date` being correctable, "a row entered today can record last week's purchase", now holds only within the same month. On 1 October a purchase from 30 September can no longer be recorded. This was accepted as the price of closed Periods staying closed.
- Because a movement's date and its Expense's Period always fall in the same month, placing Fund movements by date (`CONTEXT.md`, **Fund**) and counting Allocated by Expense Period can no longer disagree.
- Every boundary is computed in UTC, which is wrong at the edges of the month for any User not in UTC. That is a known gap, written up in `docs/user-time-zones.md`.
- Rows that existed before this rule may break it. They aren't migrated: any that were recorded in an earlier Period are read-only under the edit rule anyway.

## Considered Options

- **A day of slack at each Period boundary**, to cover the time zone problem. Rejected because it makes the Period edge fuzzy, which is the property this ADR exists to protect.
- **Gating edits on `date`**. This is equivalent for new rows. `created_at` was chosen because it can't be changed by the request being gated.
