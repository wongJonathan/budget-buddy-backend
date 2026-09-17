# Transaction listing orders by date, then created_at, then id

**Status**: accepted

`GET /transactions` returns a page of a window, so it needs a **total** order. Without one
the planner is free to break ties differently at offset 0 and at offset 50, and a row can
then surface on two pages or on neither — a paging bug that looks like a data bug.

`transactions.date` cannot supply it. It records **when the money moved, as the User
asserts it**: day-granular, and editable, because a row entered today can record last
week's purchase. Several Transactions a day is ordinary, and the User never stated an
order among them.

The order is:

```sql
ORDER BY date DESC, created_at DESC, id DESC
```

Each key does a different job, and none of the three is redundant.

## `date` leads, because the list is about money and not about the write log

A backdated row entered last still sorts by its date. The alternative — leading with
`created_at` — was rejected because `date` is user-editable: under it, correcting a
transaction's date would visibly do nothing to the listing, which is the opposite of what
editing a date means.

## `created_at` is the entry order, and `id` never was

The previous ordering was `date DESC, id DESC`, and it is tempting to read `id` as
capturing insertion order. It does not. `id` defaults to `gen_random_uuid()` — a v4 UUID,
122 random bits, no time component. Five rows inserted in sequence come back in a
shuffled order:

```
inserted 1, 2, 3, 4, 5   ->   ORDER BY id   ->   5, 2, 4, 3, 1
```

That is **stable** — the same every query — and **meaningless**. Stability is all paging
requires, so the old ordering was never wrong; it simply could not answer "which did I
enter first", and nothing else in the schema could either.

`created_at` answers it, and is trustworthy for it precisely because no client can write
it: it is absent from both `TransactionCreate` and `TransactionUpdate`, while `date` stays
editable. A column that records entry order must not be settable by the thing being
ordered.

## `id` stays last, because `created_at` ties by construction

`now()` is **transaction-start** time, not statement time, so every row a single request
writes shares a `created_at` exactly:

```
5 rows, one transaction:   distinct now() = 1,   distinct clock_timestamp() = 5
```

That is not a corner case here. `_split_spend` builds a `SPEND_SAVED` and a `SPEND` from
one payload in one commit, so the pair ties on `date` *and* on `created_at` — the two rows
most likely to be adjacent in a listing are the two the first two keys cannot separate.
`id` makes their order arbitrary *and repeatable*, which is exactly and only what paging
needs.

`clock_timestamp()` would have made `created_at` unique in practice and removed the need
for the backstop. It was rejected: every other timestamp in the schema (`created_at`,
`occurred_at`, `rolled_at`, `last_seen_at`) is `now()`, and the split pair genuinely *is*
one intention written at one instant. Their relative order is arbitrary in the domain, not
merely in the index, so inventing a distinction would assert something untrue.

## The index carries all three, and stops there

`ix_transactions_user_date_created` is `(user_id, date, created_at)`. The trailing columns
are not there to be searched — no query filters on them — but so the index can **supply
the ordering**. With `user_id` pinned by equality the matching entries are one contiguous
run already sorted by the rest of the key, so Postgres walks it backwards and stops at
`LIMIT`. Measured on 200k rows, one user, newest 50:

| index | plan |
| --- | --- |
| `(user_id, date)` | `Incremental Sort` over an `Index Scan Backward` reading **223** rows |
| `(user_id, date, id)` | `Index Scan Backward` reading **50** rows, no sort |

A fourth column would remove the last sort, and was rejected. The residual sort group is
bounded by rows sharing a date *and* a `created_at` — that is, rows written in one database
transaction, of which there are at most two. Widening every index entry to avoid sorting a
pair is a bad trade.

## The ordering is fixed, not client-selectable

No `?sort=` parameter. One contract and one index, so the ordering can be stated as a
guarantee rather than a default; every additional sort key would need an index of its own
to stay cheap. Adding a parameter later is additive, removing one is not.

## Migration

Existing rows take the migration's own `now()`. That is a lie about when they were
entered, and an unrecoverable one — nothing ever recorded it. The only data is test data,
and because every backfilled row shares the value, they fall back to `id` and stay
consistently ordered rather than becoming unstable.

The new index is created before the old one is dropped, so no statement runs unindexed.

## Not in scope

- **Renaming `date`.** With two "when" columns side by side, `occurred_on` would be
  self-documenting, but renaming breaks the client payload to buy clarity that the
  glossary provides for free. `CONTEXT.md` records the distinction instead.
- **`created_at` on other tables.** `Expense` and `Savings` have no ordering problem, and
  `Budget` already has the column.
- **Keyset pagination.** Offset paging is what ships; the total order this ADR establishes
  is also the precondition for keyset later, since a cursor has to encode a unique sort key.
