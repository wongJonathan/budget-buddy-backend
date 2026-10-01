# Known gap: every "today" and every Period is UTC

**Status**: not implemented. This documents a problem and the intended fix. It is not a decision record.

## The problem

`current_period()` and every "not after today" check read the UTC clock. Under ADR-0017, Transactions can only be written in the open Period and never in the future. That turns a cosmetic offset into refusals of correct input:

- **Behind UTC** (New York, UTC−4): at 21:00 local on 30 September it is already 1 October in UTC. A purchase correctly dated 30 September is refused because it falls in a closed Period, and September's Expenses are no longer editable.
- **Ahead of UTC** (Sydney, UTC+10): at 08:00 local on 1 October it is still 30 September in UTC. A purchase correctly dated 1 October is refused because it's in the future, and October's Expenses can't be written yet.

The window is a few hours at each month boundary, plus the "future" edge every day for Users ahead of UTC. Rollover will inherit the same problem when it's written, because it decides when a Period closes.

## The intended fix

Give `User` a time zone (an IANA name such as `America/New_York`, captured at Provisioning and editable by its owner), and compute "today" and the current Period from it instead of from UTC:

- `current_period()` takes the User, or their time zone. It's called from schema validators (`CurrentPeriod`), services and guards, so every call site needs the User in scope. Most already have it through `CurrentUser`.
- The ADR-0017 edit gate truncates `created_at` in the User's time zone, not UTC.
- `close_savings`'s drain and other server-written rows take their date from the User's "today".
- Rollover runs per User at that User's month boundary, not at one global moment.

## Rejected in the meantime

A day of slack either side of the boundary would hide the problem without fixing it, and would make the Period edge fuzzy (see ADR-0017).
