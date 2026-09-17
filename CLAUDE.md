# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Dependencies are managed with `uv`; nothing needs a manual venv activation, prefix commands with `uv run`.

```bash
uv sync                                    # install/sync dependencies

uv run ruff check .                        # lint
uv run ruff check --fix .                  # lint, autofix
uv run ruff format .                       # format
uv run mypy app                            # type-check (app/ only, not alembic/ or tests/)
uv run pytest                              # run tests
uv run pytest tests/test_health.py -v      # run a single test file
uv run pytest -k test_liveness             # run a single test by name

uv run pre-commit run --all-files          # ruff + mypy, same as CI would run

# provision an account (there is no signup route - see docs/adr/0006)
uv run python -m app.scripts.create_user --display-name "Alice" --email alice@example.com --password "..."
docker compose exec app uv run python -m app.scripts.create_user ...   # for anything but local dev

docker compose up -d                       # app + Postgres 16, local dev stack
docker compose exec app uv run alembic revision --autogenerate -m "message"
docker compose exec app uv run alembic upgrade head
docker compose restart app                 # picks up host code changes (no --reload in the container's uvicorn)
```

**Run Alembic from inside the `app` container, not from the host.** This machine has a native PostgreSQL install already bound to `localhost:5432`, so a host-side `uv run alembic ...` silently connects to *that* instead of the Dockerized Postgres. `docker compose exec app ...` uses the `db` hostname over Docker's internal network and avoids the collision. If `alembic` reports a password/auth error, this is almost always why.

Compose mounts `./app` and `./alembic` into the container, and the `app` service overrides the Dockerfile's `CMD` with `--reload`, so file edits are picked up automatically — no `docker compose restart app` needed. That override is dev-only; the Dockerfile's `CMD` (no `--reload`) is what any real deployment of the built image uses.

## Architecture

FastAPI + async SQLAlchemy 2.0 + Alembic + Postgres. Layered-by-type structure (`app/routers/`, `app/models/`, `app/schemas/`, `app/services/`) rather than domain/feature folders — a deliberate choice against the more common convention for growing FastAPI apps, see `docs/adr/0001-layered-by-type-structure.md` for why, and nest per-feature subfolders inside a layer if it grows large enough to need it rather than restructuring the whole tree.

Request flow: router (HTTP concerns, 404s) → Pydantic schema (validation, in `app/schemas/`) → service function (`app/services/`, plain async functions, one module per entity, takes an `AsyncSession` + schema, returns the ORM model) → SQLAlchemy model (`app/models/`). `app/dependencies.py` provides `DbSession`, the `Annotated[AsyncSession, Depends(...)]` alias used in every route/service signature.

### Domain model

`CONTEXT.md` is the canonical glossary — check it before introducing new domain terms or renaming existing ones, and update it inline when a term's meaning is resolved or changes. `docs/adr/` records the non-obvious architectural decisions (numbered, read them for the *why* behind anything that looks surprising). Key mechanisms, in brief:

- A `Budget` has no "active" flag of its own — it's active only when `User.active_budget_id` points at it.
- A `Category` is a plain user-owned label with no business-logic role. It used to carry a `system_type` (`income` | `saving_goal` | `debt`); that column and its enum type are gone (ADR 0009), and nothing should reintroduce control on the Category side.
- `Expense` is the single row type covering planned spending, savings goals and debt paydown, but it holds the *plan* only (ADR 0002, superseded in part by 0009 and 0011). Money set aside against it lives in `Savings`, its own user-scoped table — `Expense.amount_saved` is gone.
- **Savings is a Fund, and its balance is derived, never stored** (ADR 0011). One `Savings` per Expense *lineage*, reached through the nullable `expenses.savings_id` — deliberately **no UNIQUE** on that column, since every Period's row of a lineage points at the same fund. A fund's balance is `SAVE − SPEND_SAVED − TRANSFER` over the Transactions carrying its `savings_id`. `transactions.savings_id` and `expenses.savings_id` are *not* duplicates: the first is which fund the money went into (historical, immutable), the second is which fund the lineage feeds now (re-pointable later).
- **`app/services/savings.py:balance` is the one read that deliberately does not build on `visibility.live_*`.** It filters only the Transaction's own `is_deleted`, because the ancestor rule is right for listing what a user can see and wrong for counting what a user has — a deleted Expense must not silently revalue real money.
- **The Pool is money available to allocate**: `INCOME − SAVE − SPEND + TRANSFER(rows with null expense_id)`. Saving moves money *out* of the Pool, which is why spending from a fund later costs the current Period nothing.
- **A spend against a funded Expense is split by the server**, fund first then Pool, into a `SPEND_SAVED` row plus a `SPEND` row that are deliberately **not linked**. `SPEND_SAVED` and `TRANSFER` are server-written and rejected on create *and* update (`ClientTransactionType` in `app/schemas/fields.py`). `POST /transactions` therefore returns a **list**.
- **Income is not an Expense.** A `Transaction` is any change to the money available to a User, and `transactions.expense_id` is nullable: income is a Transaction with no Expense, because it has no plan to be measured against (ADR 0009). `transactions.name` is required for every type, since an income row has nothing else identifying it. This is why `visibility.live_transactions` **outer**-joins Expense and spells out the null case — an inner join silently drops every income row from every list.
- `Expense.series_id` + the `(budget_id, period, series_id)` unique constraint is the lineage key Rollover uses to recognize "this month's Groceries" as the successor of last month's (ADR 0004). Any code path that creates an Expense must assign or propagate a `series_id`.
- **`Expense.period` is always the first of its month**, enforced by a DB CHECK and by the `CurrentPeriod` field type in `app/schemas/fields.py`, which also restricts writes to the *open* month in both directions. A row carrying a real day-of-month inserts fine and then matches nothing — not `period =` comparisons, not the unique constraint above, not Rollover's reuse. Use `current_period()`, never `date.today()`.
- Budget activation instantiates fresh `Expense` rows rather than flipping a status flag, so a draft and a live budget stay independent (ADR 0003).
- **Rollover and Activation are not implemented yet** — only their schema groundwork (`period`, `series_id`, the unique constraint, and the `rolled_periods` table) exists. The routes currently in place are plain CRUD with no walk-forward or copy-on-activate logic wired up. When Rollover is written: it runs as a scheduled job, an Expense is *met* when its **Pool-drawing** Transactions reach its `monthly_cost` (`SAVE` and `SPEND` count; `SPEND_SAVED` and `TRANSFER` do not — ADR 0011), Rollover must propagate `savings_id` onto every row it creates, Activation must refuse while any fund under the active Budget still holds money, a carried-forward Expense is an ordinary Expense with `frequency = once` and no marker, and idempotency comes from `rolled_periods` — never from checking whether the target period looks empty (ADR 0010).
- Soft-delete: `Budget.is_deleted`, `Expense.is_deleted`, `Transaction.is_deleted` and `Savings.is_deleted` — their `DELETE` routes set the flag (see `app/services/`), and list queries filter it out through `app/services/visibility.py`. `User` and `Category` hard-delete. See ADR 0007.
- **The ancestor rule runs Budget → Expense and stops** (ADR 0007 as amended by 0011). A Transaction is visible unless it is *itself* deleted: it records a movement of real money, not a line in a plan, so deleting an Expense hides the plan and leaves its Transactions on the record. Reinstating the ancestor rule for Transaction would unspend every Spend under a deleted Expense and inflate the Pool by that amount.
- **Two guards the model depends on** (ADR 0011): an Expense outside the current Period cannot be edited or deleted (`_require_open_period`), and the active Budget cannot be deleted. Deleting the current-Period Expense of a funded lineage drains its fund back to the Pool as a `TRANSFER` pair and closes the fund — never as `INCOME`, which would assert money arrived from outside.

### Non-obvious implementation details

- **`Expense.monthly_cost` is a real Postgres `GENERATED ALWAYS AS (...) STORED` column**, not computed in Python — see `_MONTHLY_COST_EXPR` in `app/models/expense.py`. It branches on `frequency` via a SQL `CASE`. Because generated columns must be immutable (no `now()`/`CURRENT_DATE` allowed), every branch needing a calendar reads the row's own `period` instead of today: `custom` counts the months to `goal_date` from it, and `daily`/`weekly` take the number of days in the month it marks (ADR 0012). Those two are *not* equivalent to the fixed multipliers they replaced — the same plan costs less in February than in March, by design, so `monthly_cost` is a property of the plan *in a Period* and month-over-month comparisons must normalise per day. Changing the expression needs a hand-written migration that drops and re-adds the column: `ALTER COLUMN ... SET EXPRESSION` is PostgreSQL 17+ and a syntax error on this project's 16.x, and autogenerate does not diff `Computed` — it emits an empty `upgrade()`.
- **Enum columns must use `pg_enum()`** from `app/models/enums.py`, not a bare `sqlalchemy.Enum(...)`. Plain `Enum` stores the Python member *name* (`DAILY`) as the Postgres enum label; `pg_enum()` overrides that to store `.value` (`daily`) instead, so DB values match the API's JSON and any hand-written SQL (like the `monthly_cost` CASE expression) that compares against lowercase literals.
- **Primary keys are `gen_random_uuid()` — v4, no time component.** Never order by `id` expecting insertion order: it is stable but shuffled (five rows inserted 1-5 come back 5, 2, 4, 3, 1). It is a *deterministic* tie-break, which is all pagination needs of it, and never evidence about when a row was written. `Transaction.created_at` is the column that answers that, and only for Transaction (ADR 0013).
- **Watch for field names that shadow their own type import** (Python 3.14's PEP 649 deferred annotation evaluation breaks on this). `Transaction.date` hit this: `from datetime import date` + a field named `date` makes the annotation `date | None` resolve using the field itself, not the imported type, and crashes at import time. Fix is `import datetime` and annotate as `datetime.date`, not `from datetime import date`.
- **Circular FKs need a manual migration fix.** `budgets.user_id` and `users.active_budget_id` reference each other; Alembic's autogenerate can't order two mutually-dependent `CREATE TABLE`s and will emit an inline FK on the table created first, which fails at `upgrade()` time (referenced table doesn't exist yet). The existing migration splits the cross-reference into a separate `op.create_foreign_key(...)` call after both tables exist, with a matching `op.drop_constraint(...)` before both tables are dropped in `downgrade()`. Any new circular FK needs the same manual edit after autogenerating.

### Testing

`tests/conftest.py` provides a `client` fixture (`httpx.AsyncClient` over `ASGITransport`, no real network). `pytest-asyncio` runs in `asyncio_mode = "auto"`, so async tests don't need an explicit marker. Only `tests/test_health.py` exists so far — no DB-backed test fixtures/strategy have been set up yet.

### Tooling notes

- `pyproject.toml`'s mypy config is deliberately *not* full `--strict` — it's a curated subset of flags (`disallow_untyped_defs`, `check_untyped_defs`, `warn_return_any`, `warn_unused_ignores`, `no_implicit_optional`).
- Python is pinned to `>=3.14` (bleeding-edge as of this project's creation) — a deliberate choice, verified against the full dependency set with `uv pip install --dry-run` rather than assumed safe.
