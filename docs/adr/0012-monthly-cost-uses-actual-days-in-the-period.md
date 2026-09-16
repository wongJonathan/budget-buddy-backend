# `monthly_cost` uses the actual number of days in the row's own Period

**Status**: accepted (amends the `daily` and `weekly` branches of `_MONTHLY_COST_EXPR`)

`_MONTHLY_COST_EXPR` converted `daily` with `cost * 30` and `weekly` with `cost * 52.0 / 12`.
Those two constants disagree about how long a month is — 30.00 days against 30.33 — so the same
real expense produced two different allocations depending only on how the user chose to express
it:

```
$10/day   →  $300.00 / month     (cost * 30)
$70/week  →  $303.33 / month     (cost * 52 / 12)     -- the identical expense
```

That is not a rounding difference. It is the plan giving two answers to one question, and
nothing in the schema prefers either.

`daily`'s constant is separately wrong on its own terms. Twelve 30-day months is a 360-day year,
so a daily expense is under-allocated by five days annually — $50 a year on $10/day, always in
the optimistic direction. `30` is the **banker's month** from the 30/360 day count, which is a
real convention for accruing *interest* on bonds and loans, where equal periods matter more than
calendar truth. It is the wrong import for estimating household spend.

We now derive both from the actual length of the month the row is for:

```sql
WHEN 'daily'  THEN cost * EXTRACT(DAY FROM (period + INTERVAL '1 month' - INTERVAL '1 day'))
WHEN 'weekly' THEN cost * EXTRACT(DAY FROM (period + INTERVAL '1 month' - INTERVAL '1 day')) / 7
```

## Why this is available to us

`period` is non-null, is on the row, and is pinned to the first of its month by
`ck_expense_period_first_of_month`. So "how many days are in this row's month" is a pure function
of a column the generated expression already reads.

That is not a new dependency. The `custom` branch already anchors on `period`, for the reason
recorded above `_MONTHLY_COST_EXPR`: a `GENERATED` column must be immutable and cannot call
`now()`, and `period` is the row's own month, so it is the correct stand-in for "today" from that
row's point of view. This change applies the same principle to `daily` and `weekly`. An Expense
row is a plan **for one specific month**, and that month has a knowable number of days.

Leap years stop being a modelling question. February 2028 has 29 days because the calendar says
so, not because a constant was chosen well.

## Why not a better constant

The obvious alternative was to keep a fixed multiplier and make the two branches agree — `365/12`
(30.4167), `365.25/12` (30.4375), or the Gregorian mean `365.2425/12` (30.4369).

Choosing between *those three* is false precision: on $10/day, Julian against Gregorian is
**$0.075 a year**. The 100/400-year rules cannot matter to a household budget, and writing
`365.2425` into the schema would imply a rigour the rest of the model does not have.

The real objection is to constants as a class. A constant is right on average across a year and
wrong in every individual month — which is the only unit this table stores. On $10/day:

| | February | March | year (2027 / 2028) |
| --- | --- | --- | --- |
| `× 30` | $300.00 | $300.00 | $3,600 / $3,600 |
| `× 365.25/12` | $304.38 | $304.38 | $3,652.50 / $3,652.50 |
| **actual days** | **$280.00** | **$310.00** | **$3,650 / $3,660** |

A fixed constant over-allocates February by roughly $24 and under-allocates the 31-day months,
and it cannot represent a leap year at all. Actual days is exactly right both per month and
annually, and it needs no constant to be chosen, defended, or revisited.

It also fixes the daily/weekly disagreement by construction rather than by coincidence: both
branches read the same day count, so they can never drift again. Under the old pairing they
disagreed by 1.4%; under `365/12` + `52/12` they would still have disagreed by 0.27%, because 52
weeks is 364 days.

Keeping `× 30` and changing `weekly` to `× 30/7` was also rejected. It is internally consistent,
but it keeps the 360-day year and its permanent five-day shortfall, and it still cannot say
anything true about February.

## What this changes for readers of `monthly_cost`

**`monthly_cost` now varies month to month for an unchanged plan.** This is the substantive cost
of the decision and is worth stating plainly: the same $10/day expense reads $280 in February and
$310 in March. Two consequences follow.

**Met moves with it.** ADR-0011 defines Met as pool-drawing Transactions reaching `monthly_cost`,
so February's bar is genuinely lower. That is correct — February really does require less money —
but "same expense, different target each month" is now expected behaviour rather than a bug, and
Rollover's shortfall detection inherits the same property.

**Month-over-month comparisons are no longer like-for-like.** Calendar length alone moves a daily
or weekly line by up to ~5% (28 against 31 days). Anything charting a trend across months has to
normalise per-day before comparing, or it will report a February drop that did not happen. No
current read path does this; the frontend's spending-trend view is unbuilt, and this ADR is the
constraint it has to be built against.

`weekly` no longer lands on whole weeks except in February, where `28/7` is exactly 4. A 31-day
month bills 4.43 weeks. This is intended: `weekly` is a **rate**, not a schedule. The schema has
no anchor weekday, so counting actual occurrences ("how many Mondays in March") is not
expressible, and would be a different feature — a recurrence rule — rather than a better
conversion.

Computing this in the application layer instead of the column was rejected for the reason the
column exists: every write path (create, update, JSON import, Rollover, Activation) would have to
remember to maintain it, which is the drift ADR-0011 removed from `amount_saved`.

## Verified before writing this

The expression is not obviously legal: a `GENERATED` column requires an `IMMUTABLE` expression,
and `EXTRACT` is only `STABLE` over `timestamptz` because it depends on the session time zone.
`date + interval` yields a plain `timestamp`, so this stays immutable — but that is worth
demonstrating rather than reasoning about. Against the project's own PostgreSQL 16.14:

```
cost   | frequency | period     | monthly_cost
10.00  | daily     | 2027-02-01 | 280.00      -- 28 days
10.00  | daily     | 2028-02-01 | 290.00      -- 29 days, leap
10.00  | daily     | 2027-03-01 | 310.00      -- 31 days
70.00  | weekly    | 2027-02-01 | 280.00      -- identical to $10/day
70.00  | weekly    | 2027-03-01 | 310.00      -- identical to $10/day
```

Postgres accepted the column, so the immutability holds, and the daily/weekly branches now agree
exactly for the same underlying expense. The day count is also correct across the century rules —
`2100-02-01` gives 28 and `2000-02-01` gives 29 — for free, because the calendar is doing the
work.

## Migration

The database is **PostgreSQL 16.14**. `ALTER TABLE ... ALTER COLUMN ... SET EXPRESSION` arrived in
PostgreSQL 17 and is a syntax error here (confirmed against the running instance), so the
expression of a generated column cannot be altered in place: the migration drops `monthly_cost`
and re-adds it with the new expression. That rewrites the table and recomputes every row, and
moves the column to the end of the physical column order — harmless, since every read is by name.

**Every historical row is restated.** A 2026 February row that recorded $304.38 will read $280.00
afterwards. We accept that without a backfill strategy because the only data is test data.

**The same migration against live data would need a decision this one skips.** Restating a past
month silently rewrites a plan the user already budgeted and settled against, and Met is evaluated
relative to `monthly_cost`, so rows can flip between met and unmet retroactively — which changes
what Rollover would have carried. A real migration would either freeze historical rows behind a
stored value or accept and announce the restatement. Neither question is answered here.

No backend test asserts a computed `monthly_cost`: `tests/factories.py` hardcodes a value and
`tests/conftest.py` fills one in for the mocked-session path, both because the column is
Postgres-generated and never computed in those tests. The new behaviour is worth covering
directly against a real database — a February and a March row from the same `cost`, and a
`2028-02-01` row for the leap case.

## Cross-repo consequence

The frontend maintains its own copy of this conversion in `src/utils/numberConverters.ts`
(`costToMonth`), currently using `cost * 365 / 12` for `daily` — which is why the two repos
already disagree. It has to be re-synced to this rule, and it needs `period` to do so, which its
`ExpenseItemType` does not presently carry.

The better fix there is to stop computing it for saved rows at all: `ExpenseRead.monthly_cost` is
already returned on every expense and is currently discarded by the client's mapper. A client-side
conversion is then only needed for previewing an **unsaved** form, where no server value exists
yet and the current month is the right assumption.

## Not in scope

- **`custom` past its `goal_date`.** ADR-0011 already flagged that `cost / GREATEST(months, 1)`
  re-charges a fully funded goal its entire `cost` every month forever. It lives in this same
  expression and could ride along in this migration, but it is a separate semantic question —
  what a past-goal row *should* cost — and gets its own change.
- **`convert_json_to_budget`'s frequency mapping.** `_get_frequency` maps `"Set date"` to
  `Frequency.ONCE` under a `@TODO`, while the frontend sends `custom` for the same concept, so
  imported and UI-created budgets disagree. Unrelated to the day count, but adjacent enough to
  note.
